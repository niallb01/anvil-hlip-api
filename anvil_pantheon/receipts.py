"""Anvil-Pantheon-Floor — receipts (Packet 2).

Adapts DetMath v4.7 proof_certificates.py to Anvil's domain. Where
DetMath emits a ProofCertificate per solved math problem, Anvil emits an
EmissionCertificate per certified output (the type lives in
anvil_pantheon.types from Packet 1). This module provides:

  - ReceiptBuilder.build(...) -> EmissionCertificate
    Constructs a well-formed certificate with content-addressed
    certificate_id and proper graph_hash chain link.
  - validate_certificate(cert) -> (valid, errors)
    Structural validation. Returns explicit error codes per violation
    type so callers (pathway_audit, Veritas, tests) can react precisely.
  - ReceiptStore
    Append-only JSONL receipt log on disk. Methods:
      append(cert), append_idempotent(cert), load_all(),
      verify_chain(), is_present(certificate_id).

The hash-chain discipline: each new receipt's graph_hash field equals
the previous receipt's content_hash(). The first receipt in a chain has
graph_hash="" (chain root convention). verify_chain() walks the log
forward and reports the first index where the link is broken.

Idempotent re-banking (per Möbius pattern): banking the same certificate
twice is a no-op. The store tracks certificate_ids and refuses
duplicates without raising; this is how an interrupted process can
re-bank safely on retry.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .types import (
    CertificationStatus,
    EmissionCertificate,
    SlotFill,
    SubstrateKind,
    SubstrateOutput,
    _canonical_json,
    _sha256_of,
)


# ─── Builder ──────────────────────────────────────────────────────────────

class ReceiptBuilder:
    """Constructs EmissionCertificates with proper chain linkage and
    content-addressed certificate_id."""

    @staticmethod
    def build(
        *,
        emission_id: str,
        timestamp: str,
        scout_output_hash: str,
        source_card_set_hash: str,
        substrate_outputs: Dict[str, SubstrateOutput],
        template_choice: Dict[str, Any],
        slot_fills: List[SlotFill],
        veritas_pass: str,
        output_hash: str,
        pathway_audit: str = "pending",
        prior_receipt: Optional[EmissionCertificate] = None,
        provenance_chain: Tuple[str, ...] = (),
    ) -> EmissionCertificate:
        """Build a receipt. If prior_receipt is None, this is a chain
        root (graph_hash=""). Otherwise graph_hash = prior content_hash.

        provenance_chain (P16) is folded into the certificate_id payload
        ONLY when non-empty, mirroring EmissionCertificate.content_hash,
        so receipts with no provenance keep their pre-P16 ids."""
        graph_hash = "" if prior_receipt is None else prior_receipt.content_hash()

        # Build the payload that derives certificate_id (same shape as
        # EmissionCertificate.content_hash, must include graph_hash).
        payload = {
            "emission_id": emission_id,
            "timestamp": timestamp,
            "scout_output_hash": scout_output_hash,
            "source_card_set_hash": source_card_set_hash,
            "substrate_outputs": {
                k: v.content_hash() for k, v in sorted(substrate_outputs.items())
            },
            "template_choice": template_choice,
            "slot_fills": [sf.content_hash() for sf in slot_fills],
            "veritas_pass": veritas_pass,
            "output_hash": output_hash,
            "pathway_audit": pathway_audit,
            "graph_hash": graph_hash,
        }
        if provenance_chain:
            payload["provenance_chain"] = list(provenance_chain)
        certificate_id = "rcpt_" + _sha256_of(payload)[:16]

        return EmissionCertificate(
            certificate_id=certificate_id,
            emission_id=emission_id,
            timestamp=timestamp,
            scout_output_hash=scout_output_hash,
            source_card_set_hash=source_card_set_hash,
            substrate_outputs=substrate_outputs,
            template_choice=template_choice,
            slot_fills=slot_fills,
            veritas_pass=veritas_pass,
            output_hash=output_hash,
            pathway_audit=pathway_audit,
            graph_hash=graph_hash,
            provenance_chain=tuple(provenance_chain),
        )


# ─── Validation ───────────────────────────────────────────────────────────

VALID_PATHWAY_AUDIT_VALUES = {"verified", "incomplete", "broken", "pending"}


def validate_certificate(cert: EmissionCertificate) -> Tuple[bool, List[str]]:
    """Structural validation. Returns (clean, error_codes). Error codes
    are explicit strings; callers can branch on each."""
    errors: List[str] = []

    # certificate_id should match content_hash-derived ID
    expected_id = "rcpt_" + cert.content_hash()[:16]
    if cert.certificate_id != expected_id:
        errors.append("certificate_id_mismatch")

    # All explicit hash fields must be 64-char hex SHA-256
    for fname in ("scout_output_hash", "source_card_set_hash", "output_hash"):
        h = getattr(cert, fname)
        if not _is_sha256_hex(h):
            errors.append(f"{fname}_invalid")

    # graph_hash is either empty (chain root) or valid SHA-256
    if cert.graph_hash != "" and not _is_sha256_hex(cert.graph_hash):
        errors.append("graph_hash_invalid")

    # pathway_audit field has bounded values
    if cert.pathway_audit not in VALID_PATHWAY_AUDIT_VALUES:
        errors.append("pathway_audit_status_invalid")

    # Slot-fill internal consistency
    for sf in cert.slot_fills:
        if sf.certification == CertificationStatus.REFUSED:
            if sf.source_card_id is not None:
                errors.append("refused_slot_has_card_id")
            if sf.span_text_hash is not None:
                errors.append("refused_slot_has_span_hash")
        elif sf.certification == CertificationStatus.GROUNDED:
            if sf.source_card_id is None:
                errors.append("grounded_slot_missing_card_id")
            if sf.span_text_hash is None:
                errors.append("grounded_slot_missing_span_hash")

    # substrate_outputs key should match SubstrateKind.value of the
    # contained SubstrateOutput
    for k, so in cert.substrate_outputs.items():
        if k != so.substrate_kind.value:
            errors.append("substrate_output_key_mismatch")
            break

    return (not errors), errors


def _is_sha256_hex(s: Any) -> bool:
    if not isinstance(s, str) or len(s) != 64:
        return False
    try:
        int(s, 16)
    except ValueError:
        return False
    return True


# ─── Serialization ────────────────────────────────────────────────────────

def serialize_certificate(cert: EmissionCertificate) -> str:
    """Canonical JSON serialization of a certificate. Round-trip with
    deserialize_certificate is byte-identical under canonical_json."""
    d = _certificate_to_dict(cert)
    return _canonical_json(d)


def deserialize_certificate(s: str) -> EmissionCertificate:
    """Reconstruct an EmissionCertificate from canonical JSON."""
    d = json.loads(s)
    return _certificate_from_dict(d)


def _certificate_to_dict(cert: EmissionCertificate) -> Dict[str, Any]:
    return {
        "certificate_id": cert.certificate_id,
        "emission_id": cert.emission_id,
        "timestamp": cert.timestamp,
        "scout_output_hash": cert.scout_output_hash,
        "source_card_set_hash": cert.source_card_set_hash,
        "substrate_outputs": {
            k: {
                "substrate_kind": so.substrate_kind.value,
                "native_object_hash": so.native_object_hash,
                "output_payload": so.output_payload,
            }
            for k, so in cert.substrate_outputs.items()
        },
        "template_choice": cert.template_choice,
        "slot_fills": [
            {
                "slot_name": sf.slot_name,
                "source_card_id": sf.source_card_id,
                "span_text_hash": sf.span_text_hash,
                "certification": sf.certification.value,
            }
            for sf in cert.slot_fills
        ],
        "veritas_pass": cert.veritas_pass,
        "output_hash": cert.output_hash,
        "pathway_audit": cert.pathway_audit,
        "graph_hash": cert.graph_hash,
        **({"provenance_chain": list(cert.provenance_chain)} if cert.provenance_chain else {}),
    }


def _certificate_from_dict(d: Dict[str, Any]) -> EmissionCertificate:
    substrate_outputs = {
        k: SubstrateOutput(
            substrate_kind=SubstrateKind(v["substrate_kind"]),
            native_object_hash=v["native_object_hash"],
            output_payload=v["output_payload"],
        )
        for k, v in d["substrate_outputs"].items()
    }
    slot_fills = [
        SlotFill(
            slot_name=sf["slot_name"],
            source_card_id=sf["source_card_id"],
            span_text_hash=sf["span_text_hash"],
            certification=CertificationStatus(sf["certification"]),
        )
        for sf in d["slot_fills"]
    ]
    return EmissionCertificate(
        certificate_id=d["certificate_id"],
        emission_id=d["emission_id"],
        timestamp=d["timestamp"],
        scout_output_hash=d["scout_output_hash"],
        source_card_set_hash=d["source_card_set_hash"],
        substrate_outputs=substrate_outputs,
        template_choice=d["template_choice"],
        slot_fills=slot_fills,
        veritas_pass=d["veritas_pass"],
        output_hash=d["output_hash"],
        pathway_audit=d["pathway_audit"],
        graph_hash=d["graph_hash"],
        provenance_chain=tuple(d.get("provenance_chain", ())),
    )


# ─── Store ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChainVerdict:
    """Result of verify_chain() over a ReceiptStore."""
    clean: bool
    receipt_count: int
    first_broken_index: Optional[int]   # None iff clean
    error: Optional[str]                # human-readable description of break


class ReceiptStore:
    """Append-only JSONL receipt log. Each line is one canonical-JSON
    serialized certificate. Chain integrity is verified by walking
    the log forward."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._present_ids: set = set()
        if self.path.exists():
            # Populate _present_ids from existing log for idempotency
            # checks; do NOT load full certificates (load lazily on demand)
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    cid = obj.get("certificate_id")
                    if cid:
                        self._present_ids.add(cid)
                except (ValueError, KeyError):
                    # Corrupt line; verify_chain will catch this
                    continue
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def is_present(self, certificate_id: str) -> bool:
        return certificate_id in self._present_ids

    def append(self, cert: EmissionCertificate) -> None:
        """Append a certificate. Raises ValueError if the certificate_id
        is already present (use append_idempotent for retry-safe
        behavior)."""
        if cert.certificate_id in self._present_ids:
            raise ValueError(
                f"certificate_id {cert.certificate_id} already in store; "
                "use append_idempotent for retry-safe behavior"
            )
        line = serialize_certificate(cert)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        self._present_ids.add(cert.certificate_id)

    def append_idempotent(self, cert: EmissionCertificate) -> bool:
        """Append iff the certificate is not already present.
        Returns True if appended, False if already present (no-op)."""
        if cert.certificate_id in self._present_ids:
            return False
        self.append(cert)
        return True

    def load_all(self) -> List[EmissionCertificate]:
        """Load all certificates in append order."""
        if not self.path.exists():
            return []
        out: List[EmissionCertificate] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(deserialize_certificate(line))
        return out

    def verify_chain(self) -> ChainVerdict:
        """Walk the log forward; verify each receipt's graph_hash
        matches the prior receipt's content_hash. First receipt must have
        graph_hash="" (chain root convention)."""
        certs = self.load_all()
        if not certs:
            return ChainVerdict(clean=True, receipt_count=0, first_broken_index=None, error=None)
        if certs[0].graph_hash != "":
            return ChainVerdict(
                clean=False,
                receipt_count=len(certs),
                first_broken_index=0,
                error="chain root has non-empty graph_hash",
            )
        for i in range(1, len(certs)):
            expected = certs[i - 1].content_hash()
            if certs[i].graph_hash != expected:
                return ChainVerdict(
                    clean=False,
                    receipt_count=len(certs),
                    first_broken_index=i,
                    error=(
                        f"receipt {i} graph_hash does not match "
                        f"receipt {i-1} content_hash"
                    ),
                )
        return ChainVerdict(clean=True, receipt_count=len(certs), first_broken_index=None, error=None)
