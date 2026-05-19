"""Outcome feedback — Protocol seam for partner-supplied labels.

Per GPT cleaned proposal: ship the outcome-label seam now (cheap insurance,
expensive to retrofit). The seam is stubbed by default. When the partner
later wants to feed back closed-won / closed-lost labels, they implement
the Protocol and call `set_provider()`.

The system can operate lawfully without outcome labels — internal coherence
is enough signal for constraint-following (see GPT cleaned proposal §6).
Outcome labels accelerate adaptation and enable supervised quality lift
when available; they are NOT required for tier conformance.

Per JB-V2-13 (PII): outcomes are stored under opaque lead_id values, not
under names/emails. The Outcome dataclass deliberately has no PII-bearing
fields.

Feynman: an outcome is "this lead with opaque_id X resulted in WIN, LOSS,
or NURTURE." That's it. No personal data. The system uses the (id, label)
pair to compute its own calibration metrics.

This module is internal infrastructure. Nothing in cli.py imports it at TB-12.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Protocol, runtime_checkable


class OutcomeLabel(str, Enum):
    """The three labels the system tracks. Three buckets, not more — Pareto."""
    WON = "won"
    LOST = "lost"
    NURTURE = "nurture"  # not won, not lost, still in pipeline


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Outcome:
    """A single outcome receipt.

    Fields:
        lead_id:     Opaque identifier (e.g. blake2b hash of canonical input).
                     NEVER contains PII like name, email, company.
        label:       won / lost / nurture.
        timestamp_utc: When the outcome was recorded, ISO-8601 UTC.
        score_at_emission: The lead_score the system emitted for this lead.
                     Stored so calibration can compare predicted vs actual.
        confidence_at_emission: The confidence the system reported.
                     Used to detect overconfidence drift.

    No raw text, no partner-side metadata. If partner needs to attach
    additional context, that lives in their CRM, not here.
    """
    lead_id: str
    label: OutcomeLabel
    timestamp_utc: str
    score_at_emission: int
    confidence_at_emission: float


# ============================================================
# OutcomeProvider Protocol
# ============================================================

@runtime_checkable
class OutcomeProvider(Protocol):
    """Minimal interface for outcome-label ingestion.

    Partner implementations replace this with their CRM integration
    (Salesforce, HubSpot, etc.). The default is the null provider.

    Implementations must:
      - persist outcomes durably (so they survive process restart)
      - return outcomes in chronological order from `recent()`
      - never store PII (only opaque lead_id values)
    """

    def submit(self, outcome: Outcome) -> None:
        """Record an outcome. Idempotent for the same lead_id —
        a later submission with the same lead_id overwrites the earlier one.
        """
        ...

    def recent(self, limit: int = 100) -> List[Outcome]:
        """Return up to `limit` most-recent outcomes, oldest-first."""
        ...

    def count(self) -> int:
        """Total outcomes recorded. Useful for "do we have enough data
        to start supervised calibration?" gating."""
        ...


# ============================================================
# NullOutcomeProvider — default no-op stub
# ============================================================

class NullOutcomeProvider:
    """No-op OutcomeProvider. Accepts submissions, returns nothing.

    Used when the partner hasn't wired in real outcome ingestion yet.
    The system can satisfy Daedalus laws via internal-coherence signals
    even when this provider is in place — that's the design discipline
    from GPT cleaned proposal §6.
    """

    def submit(self, outcome: Outcome) -> None:
        # Silently accept. Returns nothing.
        pass

    def recent(self, limit: int = 100) -> List[Outcome]:
        return []

    def count(self) -> int:
        return 0


# ============================================================
# Helpers
# ============================================================

def make_outcome(
    lead_id: str,
    label: OutcomeLabel,
    score_at_emission: int,
    confidence_at_emission: float,
    timestamp_utc: Optional[str] = None,
) -> Outcome:
    """Build an Outcome with optional auto-timestamp.

    Helper rather than a class method so the dataclass stays a pure
    data shape (Pareto — minimum surface).
    """
    return Outcome(
        lead_id=lead_id,
        label=label,
        timestamp_utc=timestamp_utc or _now_utc_iso(),
        score_at_emission=score_at_emission,
        confidence_at_emission=confidence_at_emission,
    )


__all__ = [
    "OutcomeLabel",
    "Outcome",
    "OutcomeProvider",
    "NullOutcomeProvider",
    "make_outcome",
]
