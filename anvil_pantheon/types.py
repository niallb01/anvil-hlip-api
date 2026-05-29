"""Anvil-Pantheon-Floor — foundation types (Packet 1).

Adapts the DetMath v7.2 foundation_types.py pattern (typed lattice + frozen
dataclasses with deterministic hashing) to Anvil's domain. The math-specific
MathType enum is replaced by Anvil-relevant enums (EvidenceKind, SignalKind,
SourceCardKind, SubstrateKind). The dataclasses are the canonical types
every later packet consumes — SourceCard, SlotFill, SubstrateOutput,
EmissionCertificate.

All Anvil-specific dataclasses are frozen (immutable, hashable) and expose
content_hash() returning a deterministic SHA-256 over a canonical
serialization. This enables Packet 2's hash-chained receipts without
re-deriving the hashing discipline per module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ─── Typed lattice ────────────────────────────────────────────────────────

class EvidenceKind(str, Enum):
    """Scout's V/W/M classification. The subtype lattice is intentionally
    flat — VERIFIED, WEAK, and MISSING are distinct categories with no
    implicit narrowing between them."""
    VERIFIED = "verified"
    WEAK = "weak"
    MISSING = "missing"


class SignalKind(str, Enum):
    """The detector-kind from Scout (quantity/testimony/causal/etc.) plus
    enrichment-sourced signals from TB-15/TB-16/TB-18 and the explicit
    missing-phrase category."""
    QUANTITY = "quantity"
    TESTIMONY = "testimony"
    CAUSAL = "causal"
    MISSING_PHRASE = "missing_phrase"
    ENRICHMENT = "enrichment"


class SourceCardKind(str, Enum):
    """Where a SourceCard originated. SPAN cards come from detected text
    spans; ENRICHMENT cards come from external providers (Apollo, etc.);
    METADATA cards come from the lead intake (title, company, URL)."""
    SPAN = "span"
    ENRICHMENT = "enrichment"
    METADATA = "metadata"


class SubstrateKind(str, Enum):
    """Floor substrate identities. Additional substrate organs (Logos,
    Anansi, Themis, etc.) extend this enum when their packets land."""
    HESTIA = "hestia"
    VESTA = "vesta"
    INDRA = "indra"


class CertificationStatus(str, Enum):
    """The discipline applied at slot-fill time and at emission boundary.
    GROUNDED = backed by a SourceCard; HEDGE = framed conservatively
    because evidence is partial; REFUSED = slot refused to emit."""
    GROUNDED = "grounded"
    HEDGE = "hedge"
    REFUSED = "refused"


# ─── Subtype + inference helpers ──────────────────────────────────────────

def is_evidence_subtype(actual: EvidenceKind, expected: EvidenceKind) -> bool:
    """True iff `actual` satisfies `expected`. Flat lattice: VERIFIED
    satisfies VERIFIED only; WEAK satisfies WEAK only; MISSING satisfies
    MISSING only. There is NO implicit narrowing — a WEAK signal does not
    'count as' VERIFIED in any context, by design."""
    return actual == expected


def infer_signal_kind(detector_name: str) -> SignalKind:
    """Map a Scout detector name to the canonical SignalKind. Unknown
    names raise ValueError — Packet 1 refuses silent normalization."""
    n = detector_name.lower().strip()
    if n in ("quantity", "quantities"):
        return SignalKind.QUANTITY
    if n in ("testimony", "testimonies"):
        return SignalKind.TESTIMONY
    if n in ("causal",):
        return SignalKind.CAUSAL
    if n in ("missing_phrase", "missing-phrase", "missingphrase"):
        return SignalKind.MISSING_PHRASE
    if n.startswith("enrichment"):
        return SignalKind.ENRICHMENT
    raise ValueError(f"unknown detector name: {detector_name!r}")


# ─── Canonical serialization + content hashing ────────────────────────────

def _canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, ASCII-safe.
    All content_hash() methods route through this so re-banking identical
    payloads produces byte-identical bytes -> identical hashes
    (idempotent re-banking, per Möbius pattern)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_of(payload: Any) -> str:
    """SHA-256 of the canonical JSON of `payload`."""
    return hashlib.sha256(_canonical_json(payload).encode("ascii")).hexdigest()


# ─── Anvil-specific frozen dataclasses ────────────────────────────────────

@dataclass(frozen=True)
class SourceCard:
    """The Anvil analog of a DetMath SourceBook theorem-card. Each card
    carries one piece of evidence — a span text, an enrichment field, or a
    metadata field — with full provenance back to Scout's output.

    SourceCards are the atomic units the Cognitive Template Library fills
    slots from. A slot fill that doesn't trace to a SourceCard is a
    violation of Law-0 by construction.
    """
    card_id: str                        # content-addressed: "card_" + content_hash[:16]
    kind: SourceCardKind
    signal_kind: Optional[SignalKind]   # SPAN cards carry a SignalKind; METADATA cards may not
    span_text: str                      # the literal text from Scout's V/W/M trail
    evidence_kind: EvidenceKind         # VERIFIED / WEAK / MISSING
    subtype: Optional[str] = None       # detector subtype: uptime_sla, customer_count, social_proof, etc.
    channel_contributions: Dict[str, int] = field(default_factory=dict)  # channel_name -> point contribution
    scrape_origin: Optional[str] = None  # e.g., "firecrawl:apollo.io/about"
    signal_density_contribution: float = 0.0  # fraction this card adds to overall signal_density
    provenance_id: Optional[str] = None  # P16: links to a ProvenanceRecord; None = no provenance attached (ACF backward-compat)

    def content_hash(self) -> str:
        """Deterministic hash over all fields except card_id (which is
        derived from this hash). Used by Packet 2's receipt chain.

        provenance_id (P16) is folded in ONLY when present, so a card
        with no provenance hashes byte-identically to its pre-P16 form
        -- existing card_ids and receipt chains are preserved."""
        payload = {
            "kind": self.kind.value,
            "signal_kind": self.signal_kind.value if self.signal_kind else None,
            "span_text": self.span_text,
            "evidence_kind": self.evidence_kind.value,
            "subtype": self.subtype,
            "channel_contributions": dict(sorted(self.channel_contributions.items())),
            "scrape_origin": self.scrape_origin,
            "signal_density_contribution": self.signal_density_contribution,
        }
        if self.provenance_id is not None:
            payload["provenance_id"] = self.provenance_id
        return _sha256_of(payload)

    @classmethod
    def make(
        cls,
        kind: SourceCardKind,
        evidence_kind: EvidenceKind,
        span_text: str,
        signal_kind: Optional[SignalKind] = None,
        subtype: Optional[str] = None,
        channel_contributions: Optional[Dict[str, int]] = None,
        scrape_origin: Optional[str] = None,
        signal_density_contribution: float = 0.0,
        provenance_id: Optional[str] = None,
    ) -> "SourceCard":
        """Factory that computes the content-addressed card_id."""
        payload = {
            "kind": kind.value,
            "signal_kind": signal_kind.value if signal_kind else None,
            "span_text": span_text,
            "evidence_kind": evidence_kind.value,
            "subtype": subtype,
            "channel_contributions": dict(sorted((channel_contributions or {}).items())),
            "scrape_origin": scrape_origin,
            "signal_density_contribution": signal_density_contribution,
        }
        if provenance_id is not None:
            payload["provenance_id"] = provenance_id
        card_id = "card_" + _sha256_of(payload)[:16]
        return cls(
            card_id=card_id,
            kind=kind,
            signal_kind=signal_kind,
            span_text=span_text,
            evidence_kind=evidence_kind,
            subtype=subtype,
            channel_contributions=dict(channel_contributions or {}),
            scrape_origin=scrape_origin,
            signal_density_contribution=signal_density_contribution,
            provenance_id=provenance_id,
        )


@dataclass(frozen=True)
class SlotFill:
    """One slot in a Cognitive Template, filled (or not) from a SourceCard.
    The certification field carries the discipline: GROUNDED means a card
    backed the fill; HEDGE means the template emitted a conservative
    phrasing because the evidence was partial; REFUSED means the slot
    refused to emit and the template fell back."""
    slot_name: str
    source_card_id: Optional[str]       # None iff certification == REFUSED
    span_text_hash: Optional[str]       # hash of the literal text used; None iff REFUSED
    certification: CertificationStatus

    def content_hash(self) -> str:
        payload = {
            "slot_name": self.slot_name,
            "source_card_id": self.source_card_id,
            "span_text_hash": self.span_text_hash,
            "certification": self.certification.value,
        }
        return _sha256_of(payload)


@dataclass(frozen=True)
class SubstrateOutput:
    """The payload one substrate organ emits for one input. The
    native_object_hash captures the organ's internal-state snapshot at
    emission time; output_payload is the substrate-specific result
    structure. Packets 5-7 define what each substrate puts in
    output_payload; Packet 1 only fixes the envelope."""
    substrate_kind: SubstrateKind
    native_object_hash: str             # hash of the organ's internal state at emission
    output_payload: Dict[str, Any]      # substrate-specific; opaque to Packet 1

    def content_hash(self) -> str:
        payload = {
            "substrate_kind": self.substrate_kind.value,
            "native_object_hash": self.native_object_hash,
            "output_payload": self.output_payload,
        }
        return _sha256_of(payload)


@dataclass(frozen=True)
class EmissionCertificate:
    """The end-to-end receipt for one certified emission. Per the
    blueprint §5.2. Packet 2's receipts.py creates these; Packet 2's
    pathway_audit.py verifies replay; Packet 1 just fixes the schema.

    graph_hash chains this certificate to prior certificates in
    Mnemosyne (Packet 8). A receipt with graph_hash="" is a chain root.
    """
    certificate_id: str                 # "rcpt_" + content_hash[:16]
    emission_id: str                    # ULID
    timestamp: str                      # ISO8601
    scout_output_hash: str              # SHA-256 of full Scout JSON
    source_card_set_hash: str           # SHA-256 of typed SourceCard set
    substrate_outputs: Dict[str, SubstrateOutput]   # keyed by SubstrateKind.value
    template_choice: Dict[str, Any]     # template_id, version, audience_dims, style_dims, rationale
    slot_fills: List[SlotFill]
    veritas_pass: str                   # "clean" or JSON-encoded violations
    output_hash: str                    # SHA-256 of emitted text
    pathway_audit: str                  # "verified" | "incomplete" | "broken" | "pending"
    graph_hash: str                     # chain link to prior receipts (empty for chain root)
    provenance_chain: Tuple[str, ...] = ()   # P16: distinct provenance_ids referenced via slot_fills (empty = none)

    def content_hash(self) -> str:
        """Deterministic hash over all fields except certificate_id.

        provenance_chain (P16) is folded in ONLY when non-empty, so a
        certificate with no provenance hashes byte-identically to its
        pre-P16 form -- existing certificate_ids and graph_hash chains
        are preserved."""
        payload = {
            "emission_id": self.emission_id,
            "timestamp": self.timestamp,
            "scout_output_hash": self.scout_output_hash,
            "source_card_set_hash": self.source_card_set_hash,
            "substrate_outputs": {
                k: v.content_hash() for k, v in sorted(self.substrate_outputs.items())
            },
            "template_choice": self.template_choice,
            "slot_fills": [sf.content_hash() for sf in self.slot_fills],
            "veritas_pass": self.veritas_pass,
            "output_hash": self.output_hash,
            "pathway_audit": self.pathway_audit,
            "graph_hash": self.graph_hash,
        }
        if self.provenance_chain:
            payload["provenance_chain"] = list(self.provenance_chain)
        return _sha256_of(payload)
