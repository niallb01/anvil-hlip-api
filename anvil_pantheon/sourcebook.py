"""Anvil-Pantheon-Floor — SourceBook (Packet 3).

Adapts DetMath v5.8 sourcebook.py to Anvil. Where DetMath's SourceBook
holds theorem-card citations (theorem name, statement, citation_key,
band), Anvil's SourceBook holds the typed SourceCard set produced from
Scout's V/W/M output.

The SourceBook is the registry the Clockwork Oracle (Packet 10) queries
to fill template slots. Per the v5.8 doctrine: "correctness remains
owned by deterministic certificates and replay; SourceBook supplies
explanatory provenance." For Anvil this becomes: correctness comes from
Scout's V/W/M discipline + the substrate organs; SourceBook supplies
the citable atomic units the composer fills from.

Floor scope:
  - Registry keyed by card_id (content-addressed via SourceCard.make)
  - Query by signal_kind / evidence_kind / source_card_kind
  - Canonical content_hash over the sorted card_id set
  - No ledger / band / citation_key complexity (DetMath has those for
    theorem-licensing concerns Anvil doesn't share)
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .types import (
    EvidenceKind,
    SignalKind,
    SourceCard,
    SourceCardKind,
    _sha256_of,
)


class SourceBook:
    """Registry of SourceCards. Insertion is content-addressed and
    idempotent: adding the same card twice is a no-op. Iteration order
    is by card_id (sorted) so the SourceBook's content_hash is
    canonical regardless of insertion order.
    """

    def __init__(
        self,
        cards: Optional[Iterable[SourceCard]] = None,
        scout_metadata: Optional[Dict[str, Any]] = None,
    ):
        self._cards: Dict[str, SourceCard] = {}
        self.scout_metadata: Dict[str, Any] = dict(scout_metadata or {})
        if cards is not None:
            for c in cards:
                self.add(c)

    # ─── Registry mechanics ───────────────────────────────────────────

    def add(self, card: SourceCard) -> bool:
        """Add a card. Returns True if newly added, False if the card_id
        was already present (idempotent re-banking)."""
        if card.card_id in self._cards:
            return False
        self._cards[card.card_id] = card
        return True

    def __contains__(self, card_id: str) -> bool:
        return card_id in self._cards

    def __len__(self) -> int:
        return len(self._cards)

    def get(self, card_id: str) -> Optional[SourceCard]:
        return self._cards.get(card_id)

    def all_cards(self) -> List[SourceCard]:
        """All cards in canonical (sorted by card_id) order. This is
        the iteration order callers should use for any operation whose
        result must be deterministic."""
        return [self._cards[k] for k in sorted(self._cards.keys())]

    # ─── Query by type ────────────────────────────────────────────────

    def by_signal_kind(self, kind: SignalKind) -> List[SourceCard]:
        return [c for c in self.all_cards() if c.signal_kind == kind]

    def by_evidence_kind(self, kind: EvidenceKind) -> List[SourceCard]:
        return [c for c in self.all_cards() if c.evidence_kind == kind]

    def by_source_card_kind(self, kind: SourceCardKind) -> List[SourceCard]:
        return [c for c in self.all_cards() if c.kind == kind]

    # ─── Canonical hashing ────────────────────────────────────────────

    def content_hash(self) -> str:
        """SHA-256 over the sorted list of card_ids plus scout_metadata.
        Two SourceBooks with the same cards AND the same scout_metadata
        (regardless of insertion order) produce the same content_hash.
        This is what makes the source_card_set_hash field on
        EmissionCertificate well-defined."""
        return _sha256_of({
            "card_ids": sorted(self._cards.keys()),
            "scout_metadata": self.scout_metadata,
        })

    # ─── Summary ──────────────────────────────────────────────────────

    def summary(self) -> Dict[str, int]:
        """Counts by signal_kind and evidence_kind, useful for tests
        and diagnostics. Returns:
            {
              "total": N,
              "by_signal_kind": {"quantity": Q, ...},
              "by_evidence_kind": {"verified": V, "weak": W, "missing": M},
              "by_source_card_kind": {"span": S, "metadata": Mt, "enrichment": E},
            }
        """
        sig_counts: Dict[str, int] = {}
        ev_counts: Dict[str, int] = {}
        kind_counts: Dict[str, int] = {}
        for c in self._cards.values():
            if c.signal_kind is not None:
                sig_counts[c.signal_kind.value] = sig_counts.get(c.signal_kind.value, 0) + 1
            ev_counts[c.evidence_kind.value] = ev_counts.get(c.evidence_kind.value, 0) + 1
            kind_counts[c.kind.value] = kind_counts.get(c.kind.value, 0) + 1
        return {
            "total": len(self._cards),
            "by_signal_kind": sig_counts,
            "by_evidence_kind": ev_counts,
            "by_source_card_kind": kind_counts,
        }
