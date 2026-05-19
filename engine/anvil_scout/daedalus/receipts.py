"""LawTestReceipt — the canonical receipt every Daedalus predicate emits.

Per plate 10 footer: "All predicates emit a LawTestReceipt regardless of
pass/fail." This is the data shape.

Feynman: a receipt is just a record of what happened when a predicate ran.
It says which law and which tier was tested, whether the predicate could
even apply (some predicates require structure the system doesn't have),
what the pass/fail/N-A result was, what supporting detail explains the
result, and when it was recorded. Nothing fancier.

This module is deliberately small. The whole point is that every other
file in `daedalus/` produces these receipts in the same shape so the
harness can collect and summarize them uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict


# Predicate outcomes. Three values, not two — N/A matters as much as PASS/FAIL.
PASS = "PASS"
FAIL = "FAIL"
NOT_APPLICABLE = "N/A"

VALID_OUTCOMES = (PASS, FAIL, NOT_APPLICABLE)


@dataclass(frozen=True)
class LawTestReceipt:
    """One predicate evaluation, recorded.

    Attributes
    ----------
    law:
        Which law was tested. One of "0", "I", "II", "III", "agentic".
    tier:
        Tier the predicate is defined at. One of "T0", "T1", "T2", "T3", "T4".
    outcome:
        Predicate result. One of PASS, FAIL, NOT_APPLICABLE.
        N/A is used when the predicate requires structure the system
        does not have (e.g. T2 predicates on a single-loop system).
    detail:
        Short human-readable explanation of why this outcome. Should always
        cite the concrete observation that drove the result.
    predicate_id:
        Stable identifier for this predicate so receipts can be diffed
        across runs. Format: "law{law}_T{tier_num}" e.g. "law0_T0".
    timestamp_utc:
        When the receipt was emitted, ISO-8601 UTC. Deterministic mode
        sets a fixed value (see HarnessState.deterministic_timestamp).
    evidence:
        Free-form dict of supporting observations the predicate considered.
        Kept small — anything large should be summarized to a count or hash.
    """

    law: str
    tier: str
    outcome: str
    detail: str
    predicate_id: str
    timestamp_utc: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"LawTestReceipt outcome must be one of {VALID_OUTCOMES}, "
                f"got {self.outcome!r}"
            )
        if self.law not in ("0", "I", "II", "III", "agentic"):
            raise ValueError(
                f"LawTestReceipt law must be one of '0','I','II','III','agentic', "
                f"got {self.law!r}"
            )
        if self.tier not in ("T0", "T1", "T2", "T3", "T4"):
            raise ValueError(
                f"LawTestReceipt tier must be one of T0..T4, got {self.tier!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializable form. Useful for JSON dumps, hashing, diffs."""
        return asdict(self)


def now_utc_iso() -> str:
    """Default timestamp factory. Override via HarnessState for determinism."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
