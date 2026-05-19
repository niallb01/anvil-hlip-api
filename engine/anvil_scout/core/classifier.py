"""Signal classifier — detector spans → VERIFIED / WEAK / MISSING decisions.

This is the first stage where Law III (absence ≠ weak) becomes operative:

    quantity spans  → VERIFIED   (direct factual observation)
    missing spans   → MISSING    (explicit absence)
    testimony/causal spans:
        if a quantity span overlaps the same character range → VERIFIED
        otherwise                                             → WEAK

Co-occurrence is the Law-II orthogonal-channel test: a claim
("trusted by") backed by a quantity in the same sentence
("200 customers") is corroborated; a bare claim is not.

The output is three lists of human-readable strings ready to land in
SCHEMA.json's `signal_evidence.verified / weak / missing` fields. The
backing span pointers are NOT yet surfaced in the public schema (that's
TB-05's Law-0 emission wrapper). Internally the classifier could expose
them; we keep that for the wrapper.
"""

from typing import List, Tuple

from anvil_scout.core.detectors import Span


_TEXT_TRUNC = 80   # max chars of span.text shown in descriptors


# ─── confidence ─────────────────────────────────────────────────────────────

def compute_confidence(
    thin_scrape: bool,
    verified_count: int,
    weak_count: int,
    missing_count: int,
) -> float:
    """Uncalibrated heuristic confidence.

    Honest discipline:
      - thin_scrape forces 0.2 (hard Law-0 floor)
      - no signals at all → 0.3 (we ran but found nothing — not nothing-known)
      - otherwise: clamp(0.2 + 0.6 * weighted_signal_ratio, 0.2, 0.8)
        where weighted_signal_ratio = (verified + 0.5*weak) / total
      - 0.8 ceiling is deliberate: without outcome calibration we do not
        claim ≥ 0.9. Lifting the ceiling is a TB-06+ concern.
    """
    if thin_scrape:
        return 0.2
    total = verified_count + weak_count + missing_count
    if total == 0:
        return 0.3
    weighted = (verified_count + 0.5 * weak_count) / total
    return round(min(0.8, max(0.2, 0.2 + 0.6 * weighted)), 2)


# ─── classifier ─────────────────────────────────────────────────────────────

def _truncate(s: str, n: int = _TEXT_TRUNC) -> str:
    """Shorten a span's text for the descriptor. Adds ellipsis if cut."""
    s = " ".join(s.split())   # collapse whitespace
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _describe(span: Span) -> str:
    """Build a partner-readable descriptor for a non-missing span."""
    return f"{span.kind}/{span.subtype}: {_truncate(span.text)}"


def _describe_missing(span: Span) -> str:
    """Build a partner-readable descriptor for a missing span."""
    return f"{span.subtype} category absent from page"


_COOCCURRENCE_WINDOW = 150   # chars; approximates same-sentence proximity


def _has_overlapping_quantity(span: Span, quantity_spans: List[Span]) -> bool:
    """Co-occurrence test (JB-03-2 v2): a quantity is 'adjacent' to `span` if

      (a) their character ranges strictly overlap, OR
      (b) they are within _COOCCURRENCE_WINDOW chars of each other.

    Rationale: detectors set start/end to the keyword match position, not the
    full sentence range. Strict overlap therefore misses sentence-level
    co-occurrence where the testimony keyword and the quantity keyword sit a
    dozen characters apart inside the same sentence. The 150-char window
    matches typical B2B sentence length (15-25 words) and accepts a small
    risk of false-positives on adjacent short sentences — a tolerable bias
    because the alternative (no corroboration detection at all) is strictly
    worse.
    """
    for q in quantity_spans:
        # (a) strict overlap
        if q.start < span.end and q.end > span.start:
            return True
        # (b) proximity
        gap = min(
            abs(q.start - span.end),
            abs(span.start - q.end),
        )
        if gap < _COOCCURRENCE_WINDOW:
            return True
    return False


def classify_signals(spans: List[Span]) -> Tuple[List[str], List[str], List[str]]:
    """Apply Law-III decision rules; return (verified, weak, missing) lists.

    Output strings are sorted within each bucket for determinism (JB-03-1).
    """
    verified: List[str] = []
    weak: List[str] = []
    missing: List[str] = []

    quantity_spans = [s for s in spans if s.kind == "quantity"]

    for s in spans:
        if s.kind == "missing":
            missing.append(_describe_missing(s))
            continue

        if s.kind == "quantity":
            verified.append(_describe(s))
            continue

        # testimony or causal: corroborated if any quantity overlaps
        if _has_overlapping_quantity(s, quantity_spans):
            verified.append(_describe(s) + " (corroborated by adjacent quantity)")
        else:
            weak.append(_describe(s) + " (no supporting quantity)")

    # Dedup + sort for determinism
    verified = sorted(set(verified))
    weak = sorted(set(weak))
    missing = sorted(set(missing))

    return verified, weak, missing


__all__ = ["classify_signals", "compute_confidence"]
