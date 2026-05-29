"""Anvil-Pantheon-Floor — Oracle composer (Packet 10).

The Oracle is the decision authority + composer. It consumes a Hermes
SubstrateBundle (the substrate federation's verdict, abstracted), picks
a template from the cognitive registry, fills it from the SourceBook,
runs Veritas, and packages an EmissionCertificate. It is the last
load-bearing module of the Pantheon floor.

The Oracle does NOT compute substrate outputs (no Hestia/Vesta/Indra
imports beyond the Hermes bundle's .summary() interface -- JB-P10-4).
The Oracle does NOT modify templates or invent text -- Veritas catches
if it tries. The Oracle DOES decide emit vs refuse based on:

  1. Hermes has_real_signal: below the floor (signal_magnitude <= 10),
     refuse regardless of band agreement (the ablation case)
  2. Hermes band_agreement: if Hestia and Vesta disagree on the band,
     refuse (floor policy; future packets may pick a split-handling
     template instead)
  3. Template fill: if the chosen template critically refuses, refuse
  4. Veritas verdict: if Veritas finds violations, refuse

Every Oracle call produces an EmissionCertificate -- either an emitted
certificate (with rendered text, slot fills, pathway_audit=verified)
or a REFUSED certificate (slot_fills all REFUSED, rendered text empty,
veritas_pass capturing the refusal_reasons).

NON_CLAIMS:
  - Does NOT compute substrate outputs
  - Does NOT decide which substrate is correct (Hermes encapsulates)
  - Does NOT skip Veritas checks (always runs)
  - Does NOT modify the certificate after Veritas; refusal is final
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .cognitive.template_library import (
    Template,
    TemplateFillResult,
    TemplateRegistry,
    fill_template,
)
from .receipts import ReceiptBuilder
from .services.hermes import SubstrateBundle
from .sourcebook import SourceBook
from .types import (
    CertificationStatus,
    EmissionCertificate,
    SlotFill,
)
from .veritas import VERITAS_PASS_CLEAN, VeritasVerdict, verify


# ─── Constants ────────────────────────────────────────────────────────────

# Closed set of refusal reason codes the Oracle can emit. Tests assert
# expected codes appear; consumers can branch on them.
REFUSAL_CODES: Tuple[str, ...] = (
    "indra_signal_below_floor",
    "indra_coherence_below_floor",
    "band_disagreement",
    "template_refused",
    "veritas_violations",
    "no_template_available",
)

# Default template the Oracle uses when no specific selection is made.
# At floor only sales_email_v0_1 exists; future packets add more.
DEFAULT_TEMPLATE_ID = "sales_email"
DEFAULT_TEMPLATE_VERSION = "v0.1"


# ─── Decision + Result types ──────────────────────────────────────────────

@dataclass(frozen=True)
class OracleResult:
    """Output of Oracle.compose. Always carries an EmissionCertificate
    (success or refusal); refused=True iff the Oracle chose not to
    emit. refusal_reasons lists the named codes; rendered_text is
    empty when refused."""
    certificate: EmissionCertificate
    refused: bool
    refusal_reasons: Tuple[str, ...]
    rendered_text: str
    veritas_verdict: VeritasVerdict


# ─── Helpers ──────────────────────────────────────────────────────────────

def _ulid_like(timestamp_secs: float, seed: str) -> str:
    """Deterministic ULID-shaped ID from timestamp + seed. NOT a real
    ULID -- we just need a stable, unique-looking 26-char identifier
    for the receipt schema. Tests can pass emission_id directly for
    full determinism."""
    base = f"{int(timestamp_secs * 1000):013d}_{seed}"
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:13].upper()
    return f"01HXY{digest}"


def _now_iso(timestamp_secs: float) -> str:
    """ISO8601 from epoch seconds (UTC, second resolution)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp_secs))


def _all_refused_fills(template: Template) -> Tuple[SlotFill, ...]:
    """Build a slot_fills list where every slot is REFUSED. Used when
    refusing without ever attempting a fill (e.g., bundle refusal)."""
    return tuple(
        SlotFill(
            slot_name=spec.slot_name,
            source_card_id=None,
            span_text_hash=None,
            certification=CertificationStatus.REFUSED,
        )
        for spec in template.slot_specs
    )


# ─── Public entry point ───────────────────────────────────────────────────

def compose(
    *,
    sourcebook: SourceBook,
    bundle: SubstrateBundle,
    registry: TemplateRegistry,
    scout_output_hash: str,
    template_id: str = DEFAULT_TEMPLATE_ID,
    template_version: str = DEFAULT_TEMPLATE_VERSION,
    emission_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    prior_receipt: Optional[EmissionCertificate] = None,
    now_secs: Optional[float] = None,
    provenance_store: Optional[Any] = None,
) -> OracleResult:
    """The Oracle's main entry point. Always returns an OracleResult
    with a built EmissionCertificate (success or refusal).

    Decision sequence:
      1. Bundle.has_real_signal() -> if False, refuse immediately
      2. Bundle.has_coherent_signal() -> if False, refuse (channels
         contradict each other despite real signal)
      3. Bundle.summary()['band_agreement'] -> if False, refuse
      4. fill_template(template, sourcebook) -> if template_refused, refuse
      5. verify(rendered, fills, sourcebook) -> if violations, refuse
      6. Otherwise emit with pathway_audit='verified'

    Determinism: pass emission_id + timestamp + now_secs for fully
    reproducible certificates in tests.
    """
    # Generate timestamps / ids deterministically if not provided
    if now_secs is None:
        now_secs = time.time()
    if timestamp is None:
        timestamp = _now_iso(now_secs)
    if emission_id is None:
        emission_id = _ulid_like(now_secs, scout_output_hash[:8])

    # Load template (may not exist in registry -> refuse)
    if not registry.has(template_id, template_version):
        return _build_refusal(
            reason_codes=(f"no_template_available:{template_id}@{template_version}",),
            template=None,
            sourcebook=sourcebook,
            bundle=bundle,
            scout_output_hash=scout_output_hash,
            emission_id=emission_id,
            timestamp=timestamp,
            template_id=template_id,
            template_version=template_version,
            prior_receipt=prior_receipt,
        )

    template = registry.get(template_id, template_version)

    # ─── Decision 1: Indra signal floor (ablation discriminator) ───────
    if not bundle.has_real_signal():
        magnitude = bundle.summary().get("indra_signal_magnitude", 0)
        return _build_refusal(
            reason_codes=(f"indra_signal_below_floor:{magnitude}",),
            template=template,
            sourcebook=sourcebook,
            bundle=bundle,
            scout_output_hash=scout_output_hash,
            emission_id=emission_id,
            timestamp=timestamp,
            template_id=template_id,
            template_version=template_version,
            prior_receipt=prior_receipt,
        )

    # ─── Decision 1.5: Indra coherence floor (self-contradiction catch) ─
    # Reached only because Decision 1 passed (real signal present). Now
    # require Indra's channels to AGREE in phase. Catches "strong but
    # self-contradictory" evidence that the magnitude floor and the
    # 2-way band vote both miss. This is Indra's own verdict on a third
    # axis (coherence), not a band vote.
    if not bundle.has_coherent_signal():
        coherence = bundle.summary().get("indra_global_coherence", 0.0)
        return _build_refusal(
            reason_codes=(f"indra_coherence_below_floor:{coherence}",),
            template=template,
            sourcebook=sourcebook,
            bundle=bundle,
            scout_output_hash=scout_output_hash,
            emission_id=emission_id,
            timestamp=timestamp,
            template_id=template_id,
            template_version=template_version,
            prior_receipt=prior_receipt,
        )

    # ─── Decision 2: substrate band agreement ──────────────────────────
    summary = bundle.summary()
    if not summary["band_agreement"]:
        hb = summary["hestia_band"]
        vb = summary["vesta_map_band"]
        return _build_refusal(
            reason_codes=(f"band_disagreement:{hb}_vs_{vb}",),
            template=template,
            sourcebook=sourcebook,
            bundle=bundle,
            scout_output_hash=scout_output_hash,
            emission_id=emission_id,
            timestamp=timestamp,
            template_id=template_id,
            template_version=template_version,
            prior_receipt=prior_receipt,
        )

    # ─── Step 3: fill the template ─────────────────────────────────────
    fill_result = fill_template(template, sourcebook)

    if fill_result.template_refused:
        return _build_refusal(
            reason_codes=tuple("template_refused:" + r for r in fill_result.refusal_reasons),
            template=template,
            sourcebook=sourcebook,
            bundle=bundle,
            scout_output_hash=scout_output_hash,
            emission_id=emission_id,
            timestamp=timestamp,
            template_id=template_id,
            template_version=template_version,
            prior_receipt=prior_receipt,
            slot_fills=fill_result.slot_fills,
        )

    # ─── Step 4: Veritas verification ──────────────────────────────────
    verdict = verify(fill_result.rendered_text, fill_result.slot_fills, sourcebook,
                     provenance_store=provenance_store)

    if not verdict.clean:
        # Veritas found violations -- refuse but carry the verdict in
        # the certificate's veritas_pass field for forensic review.
        reasons = tuple(
            f"veritas_violations:{code}" for code in sorted(verdict.violations.keys())
        )
        return _build_refusal(
            reason_codes=reasons,
            template=template,
            sourcebook=sourcebook,
            bundle=bundle,
            scout_output_hash=scout_output_hash,
            emission_id=emission_id,
            timestamp=timestamp,
            template_id=template_id,
            template_version=template_version,
            prior_receipt=prior_receipt,
            slot_fills=fill_result.slot_fills,
            veritas_verdict=verdict,
        )

    # ─── Step 5: emit ──────────────────────────────────────────────────
    output_hash = hashlib.sha256(fill_result.rendered_text.encode("utf-8")).hexdigest()
    # P16: collect distinct provenance_ids of the cards that actually
    # backed the emission (in slot-fill order). Cards with no provenance
    # contribute nothing; the chain is empty for the legacy ACF path.
    provenance_chain: List[str] = []
    for sf in fill_result.slot_fills:
        if not sf.source_card_id:
            continue
        card = sourcebook.get(sf.source_card_id)
        if card is not None and getattr(card, "provenance_id", None):
            if card.provenance_id not in provenance_chain:
                provenance_chain.append(card.provenance_id)
    cert = ReceiptBuilder.build(
        emission_id=emission_id,
        timestamp=timestamp,
        scout_output_hash=scout_output_hash,
        source_card_set_hash=sourcebook.content_hash(),
        substrate_outputs={
            "hestia": bundle.hestia_out,
            "vesta":  bundle.vesta_out,
            "indra":  bundle.indra_out,
        },
        template_choice={
            "template_id":     template.template_id,
            "version":         template.version,
            "selection_basis": "floor_default",
            "rationale":       "bundle clean + band agreement + real signal",
        },
        slot_fills=list(fill_result.slot_fills),
        veritas_pass=verdict.to_certificate_field(),
        output_hash=output_hash,
        pathway_audit="verified",
        prior_receipt=prior_receipt,
        provenance_chain=tuple(provenance_chain),
    )

    return OracleResult(
        certificate=cert,
        refused=False,
        refusal_reasons=(),
        rendered_text=fill_result.rendered_text,
        veritas_verdict=verdict,
    )


# ─── Refusal-certificate builder ──────────────────────────────────────────

def _build_refusal(
    *,
    reason_codes: Tuple[str, ...],
    template: Optional[Template],
    sourcebook: SourceBook,
    bundle: SubstrateBundle,
    scout_output_hash: str,
    emission_id: str,
    timestamp: str,
    template_id: str,
    template_version: str,
    prior_receipt: Optional[EmissionCertificate],
    slot_fills: Optional[Tuple[SlotFill, ...]] = None,
    veritas_verdict: Optional[VeritasVerdict] = None,
) -> OracleResult:
    """Build a refusal certificate. Always returns OracleResult with
    refused=True. The certificate's slot_fills are all REFUSED (or the
    template's partial fills if provided), rendered text is empty,
    veritas_pass carries the refusal reason chain as a JSON string,
    pathway_audit='broken'."""

    if slot_fills is None:
        if template is not None:
            slot_fills = _all_refused_fills(template)
        else:
            slot_fills = ()

    # Build a veritas_pass string that carries the refusal_reasons for
    # forensic review, even if Veritas didn't run / found nothing.
    if veritas_verdict is not None and not veritas_verdict.clean:
        # Preserve the Veritas violation detail alongside refusal reasons
        payload = {"refusal_reasons": list(reason_codes),
                   "veritas_violations": {k: list(v) for k, v in veritas_verdict.violations.items()}}
    else:
        payload = {"refusal_reasons": list(reason_codes)}
    veritas_pass = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    # Output hash: hash of the empty string (canonical "no emission")
    output_hash = hashlib.sha256(b"").hexdigest()

    cert = ReceiptBuilder.build(
        emission_id=emission_id,
        timestamp=timestamp,
        scout_output_hash=scout_output_hash,
        source_card_set_hash=sourcebook.content_hash(),
        substrate_outputs={
            "hestia": bundle.hestia_out,
            "vesta":  bundle.vesta_out,
            "indra":  bundle.indra_out,
        },
        template_choice={
            "template_id":     template_id,
            "version":         template_version,
            "selection_basis": "refused_pre_template_or_template_refused",
            "rationale":       "; ".join(reason_codes) if reason_codes else "unspecified",
        },
        slot_fills=list(slot_fills),
        veritas_pass=veritas_pass,
        output_hash=output_hash,
        pathway_audit="broken",
        prior_receipt=prior_receipt,
    )

    return OracleResult(
        certificate=cert,
        refused=True,
        refusal_reasons=reason_codes,
        rendered_text="",
        veritas_verdict=veritas_verdict if veritas_verdict is not None else VeritasVerdict(clean=True),
    )
