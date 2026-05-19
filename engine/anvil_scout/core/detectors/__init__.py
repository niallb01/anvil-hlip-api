"""Structural detectors — vocabulary-agnostic signal extraction.

These detectors find STRUCTURE, not domain words. They work on any English
prose, B2B or otherwise, because they look at sentence shapes and numeric
patterns rather than business jargon. They are deterministic, regex-based,
and fast.

Output is a list of `Span` objects. A Span is a (start, end, text, kind,
subtype, confidence) record pointing back into the input text — this is the
Law-0 receipt that downstream stages (classifier, scorer) consume.

Missing signals use start=-1, end=-1, text="" — absence has no position.

Detectors:
    testimony       — sentences that make a claim about the company
    quantity        — numbers attached to meaningful nouns
    causal          — cause-effect language
    missing_phrase  — B2B signal categories absent from the text

Assumption: English text. Other languages will under-fire; that is documented
behaviour, not a bug.
"""

from dataclasses import dataclass, asdict
from typing import List


@dataclass
class Span:
    """A pointer to evidence in the input text."""

    start: int
    end: int
    text: str
    kind: str          # "testimony" | "quantity" | "causal" | "missing"
    subtype: str       # detector-specific category
    confidence: float  # 0.0–1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_missing(self) -> bool:
        return self.kind == "missing" or (self.start < 0 and self.end < 0)


def run_all_detectors(text: str) -> List[Span]:
    """Run every detector and return concatenated spans.

    Order: testimony, quantity, causal, missing. No deduplication —
    overlap between detectors is expected and informative for the
    classifier (TB-03).
    """
    # Local imports to keep the package lightweight when only one detector
    # is needed in isolation.
    from anvil_scout.core.detectors.testimony import detect_testimony
    from anvil_scout.core.detectors.quantity import detect_quantity
    from anvil_scout.core.detectors.causal import detect_causal
    from anvil_scout.core.detectors.missing_phrase import detect_missing

    spans: List[Span] = []
    spans.extend(detect_testimony(text))
    spans.extend(detect_quantity(text))
    spans.extend(detect_causal(text))
    spans.extend(detect_missing(text))
    return spans


def hit_counts(spans: List[Span]) -> dict:
    """Group spans by kind and return counts. Useful for the CLI rationale."""
    counts = {"testimony": 0, "quantity": 0, "causal": 0, "missing": 0}
    for s in spans:
        if s.kind in counts:
            counts[s.kind] += 1
    return counts


__all__ = ["Span", "run_all_detectors", "hit_counts"]
