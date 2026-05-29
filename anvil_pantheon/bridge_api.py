"""Anvil-Pantheon-Floor — Bridge API (Packet 13).

The bridge API is the single entry point for external systems
(particularly Niall's anvil-hlip-api) to drive the Pantheon floor
pipeline. It accepts a Scout output dict, runs the full pipeline,
and returns a JSON-safe dict containing the EmissionCertificate, the
refusal flag and reasons, the rendered text (if emitted), and the
ingress-guard quarantine record.

This module is FRAMEWORK-AGNOSTIC: no FastAPI / Flask / Django
imports. The fastapi_shim.py module wraps this entry point in a
FastAPI router for convenience; Niall can use that, write his own
shim, or call certify_lead() directly from any orchestrator.

The bridge is a THIN ORCHESTRATOR. All logic lives in P1-P12 modules;
the bridge just sequences them and handles serialization.

NON_CLAIMS:
  - Does NOT compute substrate outputs (delegates to P5-P7)
  - Does NOT make decisions (delegates to P10 Oracle)
  - Does NOT verify (delegates to P10 Veritas via Oracle)
  - Does NOT modify Niall's anvil-hlip-api repo
  - Does NOT take FastAPI as a hard dependency
  - Does NOT auto-apply learning proposals (record-only stays
    record-only)
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .cognitive import CANONICAL_REGISTRY
from .ingress_guard import guard_ingress
from .integration.scout_adapter import adapt_scout_output
from .oracle import (
    DEFAULT_TEMPLATE_ID,
    DEFAULT_TEMPLATE_VERSION,
    OracleResult,
    compose,
)
from .services.hermes import bundle_substrates
from .services.mnemosyne import MnemosyneStore
from .substrates.hestia import compute_hestia
from .substrates.indra import compute_indra
from .substrates.vesta import compute_vesta
from .types import EmissionCertificate


logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────

BRIDGE_ERROR_CODE = "bridge_error"
"""Refusal code used when the bridge catches an unexpected exception."""


# ─── Serialization helpers ────────────────────────────────────────────────

def _json_safe(value: Any) -> Any:
    """Recursively convert dataclasses / enums / tuples / dicts into
    JSON-safe primitives. The result is guaranteed to round-trip
    through json.dumps without losing structure or type information
    above what JSON natively supports."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if is_dataclass(value):
        return {k: _json_safe(v) for k, v in asdict(value).items()}
    # Fallback: stringify (safe; preserves something rather than crash)
    return str(value)


def _certificate_to_dict(cert: EmissionCertificate) -> Dict[str, Any]:
    """Serialize an EmissionCertificate to a JSON-safe dict. Preserves
    all schema fields and recurses through SubstrateOutput / SlotFill
    nested structures."""
    return {
        "certificate_id":        cert.certificate_id,
        "emission_id":           cert.emission_id,
        "timestamp":             cert.timestamp,
        "scout_output_hash":     cert.scout_output_hash,
        "source_card_set_hash":  cert.source_card_set_hash,
        "substrate_outputs":     {
            k: _json_safe(v) for k, v in cert.substrate_outputs.items()
        },
        "template_choice":       _json_safe(cert.template_choice),
        "slot_fills":            [_json_safe(sf) for sf in cert.slot_fills],
        "veritas_pass":          cert.veritas_pass,
        "output_hash":           cert.output_hash,
        "pathway_audit":         cert.pathway_audit,
        "graph_hash":            cert.graph_hash,
        "provenance_chain":      list(cert.provenance_chain),
    }


# ─── Bridge entry point ───────────────────────────────────────────────────

def certify_lead(
    scout_output: Dict[str, Any],
    *,
    template_id: str = DEFAULT_TEMPLATE_ID,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
    prior_receipt: Optional[EmissionCertificate] = None,
    emission_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    now_secs: Optional[float] = None,
    mnemosyne_store: Optional[MnemosyneStore] = None,
    provenance_store: Any = None,
) -> Dict[str, Any]:
    """End-to-end: Scout output dict -> certified emission dict.

    This is the single entry point for external API consumers (Niall's
    anvil-hlip-api, batch processors, CLI tools, etc).

    Pipeline sequence:
      1. adapt_scout_output -> SourceBook
      2. guard_ingress -> safe SourceBook + quarantines
      3. Hestia + Vesta + Indra in parallel
      4. Hermes -> SubstrateBundle
      5. Oracle.compose (5-gate decision + template fill + Veritas)
      6. Optional: record to MnemosyneStore if provided

    Returns a JSON-safe dict with:
      - certificate: serialized EmissionCertificate
      - refused: bool
      - refusal_reasons: list[str]
      - rendered_text: str (empty if refused)
      - ingress_guard:
        - clean: bool
        - quarantined_card_ids: list[str]
        - blocked: bool
        - block_reasons: list[str]
      - error: str (only present if bridge caught an exception)

    JB-P13-1: catches any exception, returns a bridge_error refusal
    record so the API can serve it as a 200 with a clear error code
    rather than 500ing.
    """
    try:
        # 1. Adapter
        book = adapt_scout_output(scout_output)

        # 2. Ingress guard
        guard = guard_ingress(book)
        safe = guard.safe_book

        # 3-4. Substrates + bundle
        h_out = compute_hestia(safe)
        v_out = compute_vesta(safe)
        i_out = compute_indra(safe)
        bundle = bundle_substrates(h_out, v_out, i_out, safe.content_hash())

        # 5. Oracle
        result: OracleResult = compose(
            sourcebook=safe,
            bundle=bundle,
            registry=CANONICAL_REGISTRY,
            scout_output_hash=safe.content_hash(),
            template_id=template_id,
            template_version=template_version,
            emission_id=emission_id,
            timestamp=timestamp,
            prior_receipt=prior_receipt,
            now_secs=now_secs,
            provenance_store=provenance_store,
        )

        # 6. Optional Mnemosyne record (idempotent under repeated calls)
        if mnemosyne_store is not None:
            try:
                mnemosyne_store.record(result.certificate)
            except Exception as record_exc:
                logger.warning(
                    "mnemosyne_store.record failed; continuing without "
                    "blocking the response: %s", record_exc
                )

        # P16: provenance surface. IDs-only by default (JB-P15-4); full
        # record bodies only when a ProvenanceStore is supplied AND the
        # chain ids resolve in it.
        chain = list(result.certificate.provenance_chain)
        if provenance_store is not None:
            provenance_records: Any = {
                pid: _json_safe(provenance_store.get(pid))
                for pid in chain if provenance_store.is_present(pid)
            }
        else:
            provenance_records = chain  # IDs only

        return {
            "certificate":     _certificate_to_dict(result.certificate),
            "refused":         result.refused,
            "refusal_reasons": list(result.refusal_reasons),
            "rendered_text":   result.rendered_text,
            "provenance_records": provenance_records,
            "ingress_guard": {
                "clean":                 guard.clean,
                "quarantined_card_ids":  list(guard.quarantined_card_ids),
                "blocked":               bool(guard.block_reasons),
                "block_reasons":         list(guard.block_reasons),
            },
        }

    except Exception as exc:  # JB-P13-1: degrade gracefully
        logger.exception("bridge_api.certify_lead failed unexpectedly")
        return {
            "certificate":     None,
            "refused":         True,
            "refusal_reasons": [f"{BRIDGE_ERROR_CODE}:{type(exc).__name__}:{exc}"],
            "rendered_text":   "",
            "ingress_guard":   {"clean": False, "quarantined_card_ids": [],
                                "blocked": False, "block_reasons": []},
            "error": {
                "type":      type(exc).__name__,
                "message":   str(exc),
                "traceback": traceback.format_exc(),
            },
        }
