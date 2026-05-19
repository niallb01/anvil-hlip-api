"""TB-13 detector observability — measure first, mutate later.

For each pipeline call we can ask three questions per detector:
    - How many spans did this detector emit on this input?
    - How many of those spans were classified as VERIFIED or WEAK?
    - How many survived the Law-0 emission boundary?

The difference between "classified" and "survived" is the Law-0 strip count:
spans the detector emitted that the boundary wrapper had to throw out
because they lacked sufficient backing. Strip rate per detector is the
coherence signal that TB-14 will use to tune detector sensitivity.

This module emits `DetectorReceipt` records and aggregates them into
`state.detector_state` under per-detector rolling counters. No detector
behaviour changes here — TB-13 is observation only.

Feynman: imagine each detector as a person hired to spot evidence. TB-13
gives each person a logbook: "How many times did you raise your hand?
How many of those got accepted by the classifier? How many got thrown
out by the boundary check?" TB-14 will use the logbook to decide who
needs a stricter standard, who's missing things, who needs to be retrained.
TB-13 just keeps the books.

Per JB-V2-13: receipts store an opaque blake2b hash of the input text,
NEVER the raw text. The hash is one-way; no PII leaks.

Per JB-13-2: strip-don't-raise applies — any exception in observability
is the caller's problem (cli.py wraps the whole observability block in
try/except so the pipeline stays robust).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


# The four detectors in v0.1.0. Keep names stable across TBs — state buckets
# are keyed by these strings.
DETECTOR_NAMES = ("testimony", "quantity", "causal", "missing_phrase")

# Map between detector_name and the `kind` field on Span objects.
# missing_phrase detector emits Span.kind == "missing".
_DETECTOR_TO_KIND = {
    "testimony": "testimony",
    "quantity": "quantity",
    "causal": "causal",
    "missing_phrase": "missing",
}


# ============================================================
# Hashing — opaque, PII-safe
# ============================================================

def hash_text(text: str) -> str:
    """Opaque 16-byte blake2b hash of the input text.

    Used as the receipt's content identifier. Two runs with identical
    text produce identical hashes (idempotent measurement, JB-13-6).
    The hash is one-way — no PII reconstruction possible.
    """
    h = hashlib.blake2b(text.encode("utf-8", errors="replace"), digest_size=16)
    return h.hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# Receipt shape
# ============================================================

@dataclass(frozen=True)
class DetectorReceipt:
    """One detector's observation of one pipeline call.

    Fields:
        detector_name:           One of DETECTOR_NAMES.
        text_hash:               Opaque blake2b hex of input text.
        text_chars:              Length of input text (informative only).
        spans_emitted:           Total spans this detector emitted.
        unique_ranges:           Distinct (start, end) tuples among emitted spans.
                                 Lower than spans_emitted iff overlapping
                                 emissions exist — JB-V2-16 evidence
                                 amplification signal.
        spans_classified:        Of emitted spans, how many became VERIFIED
                                 or WEAK in the classifier (before Law-0).
                                 For missing_phrase: count of MISSING entries.
        spans_survived_law0:     Of classified spans, how many survived the
                                 Law-0 emission boundary check.
        spans_stripped_law0:     classified - survived. The strip count.
        timestamp_utc:           ISO-8601 UTC.
    """
    detector_name: str
    text_hash: str
    text_chars: int
    spans_emitted: int
    unique_ranges: int
    spans_classified: int
    spans_survived_law0: int
    spans_stripped_law0: int
    timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def strip_rate(self) -> float:
        """Strip rate for this single call. 0.0 if nothing was classified.
        Future TBs (14+) use this to tune detector sensitivity."""
        if self.spans_classified <= 0:
            return 0.0
        return self.spans_stripped_law0 / self.spans_classified


# ============================================================
# Observation — derive receipts from pipeline trace
# ============================================================

def _count_by_kind_prefix(strings: Iterable[str], kind: str) -> int:
    """Count strings whose prefix is f'{kind}/' (matches classifier's
    `_describe()` format: 'kind/subtype: description')."""
    prefix = f"{kind}/"
    return sum(1 for s in strings if s.startswith(prefix))


def observe_detectors(
    text: str,
    spans: List,  # List[Span] but kept loose to avoid import cycle
    verified_pre: List[str],
    weak_pre: List[str],
    missing_pre: List[str],
    verified_post: List[str],
    weak_post: List[str],
    missing_post: List[str],
    timestamp_utc: Optional[str] = None,
) -> List[DetectorReceipt]:
    """Derive per-detector receipts from a pipeline trace.

    Args:
        text:           Cleaned text the detectors ran on.
        spans:          Raw spans from run_all_detectors.
        verified_pre / weak_pre / missing_pre:
            The three classified lists BEFORE Law-0 stripping.
        verified_post / weak_post / missing_post:
            The three classified lists AFTER Law-0 stripping (final state).
        timestamp_utc:  Optional fixed timestamp (deterministic tests).

    Returns:
        Exactly four receipts, one per detector in DETECTOR_NAMES order.
    """
    ts = timestamp_utc or _now_utc_iso()
    t_hash = hash_text(text)
    t_chars = len(text)
    receipts: List[DetectorReceipt] = []

    for det_name in DETECTOR_NAMES:
        kind = _DETECTOR_TO_KIND[det_name]

        # Emitted: spans this detector produced.
        det_spans = [s for s in spans if getattr(s, "kind", None) == kind]
        spans_emitted = len(det_spans)
        unique_ranges = len({(s.start, s.end) for s in det_spans})

        if det_name == "missing_phrase":
            # missing entries are classified differently — they have no
            # "kind/" prefix; the classifier just lists missing subtypes.
            # Pre/post counts for missing are the lengths of the lists.
            # Law-0 typically doesn't strip missing entries (they assert
            # absence, which the wrapper preserves), but we still measure
            # in case future TBs add stripping rules.
            classified = len(missing_pre)
            survived = len(missing_post)
        else:
            classified = _count_by_kind_prefix(verified_pre, kind) \
                       + _count_by_kind_prefix(weak_pre, kind)
            survived = _count_by_kind_prefix(verified_post, kind) \
                     + _count_by_kind_prefix(weak_post, kind)

        stripped = max(0, classified - survived)

        receipts.append(DetectorReceipt(
            detector_name=det_name,
            text_hash=t_hash,
            text_chars=t_chars,
            spans_emitted=spans_emitted,
            unique_ranges=unique_ranges,
            spans_classified=classified,
            spans_survived_law0=survived,
            spans_stripped_law0=stripped,
            timestamp_utc=ts,
        ))

    return receipts


# ============================================================
# Aggregation — receipts → state.detector_state rolling counters
# ============================================================

def _empty_detector_bucket() -> Dict[str, Any]:
    """Fresh detector counter bucket. Keys are stable across TBs —
    TB-14 will add a `sensitivity_threshold` here when promoting to T1."""
    return {
        "calls_seen": 0,
        "spans_emitted_total": 0,
        "unique_ranges_total": 0,
        "spans_classified_total": 0,
        "spans_survived_total": 0,
        "spans_stripped_total": 0,
        "recent_strip_rate": 0.0,
        "last_seen_at": None,
    }


def aggregate_into_state(state: Dict[str, Any], receipts: List[DetectorReceipt]) -> None:
    """Update state['detector_state'] with rolling counters from receipts.

    Mutates state in place. Caller is responsible for state_provider.save().

    Each detector has a bucket under state['detector_state'][name]:
        calls_seen, spans_emitted_total, unique_ranges_total,
        spans_classified_total, spans_survived_total, spans_stripped_total,
        recent_strip_rate, last_seen_at.

    Idempotency note: calling this twice with the same receipts will
    double-count. The caller is expected to call once per pipeline run.
    """
    if "detector_state" not in state or not isinstance(state["detector_state"], dict):
        state["detector_state"] = {}

    for r in receipts:
        bucket = state["detector_state"].setdefault(r.detector_name,
                                                     _empty_detector_bucket())
        bucket["calls_seen"] = bucket.get("calls_seen", 0) + 1
        bucket["spans_emitted_total"] = bucket.get("spans_emitted_total", 0) + r.spans_emitted
        bucket["unique_ranges_total"] = bucket.get("unique_ranges_total", 0) + r.unique_ranges
        bucket["spans_classified_total"] = bucket.get("spans_classified_total", 0) + r.spans_classified
        bucket["spans_survived_total"] = bucket.get("spans_survived_total", 0) + r.spans_survived_law0
        bucket["spans_stripped_total"] = bucket.get("spans_stripped_total", 0) + r.spans_stripped_law0
        if r.spans_classified > 0:
            bucket["recent_strip_rate"] = round(
                r.spans_stripped_law0 / r.spans_classified, 4
            )
        bucket["last_seen_at"] = r.timestamp_utc


__all__ = [
    "DETECTOR_NAMES",
    "DetectorReceipt",
    "hash_text",
    "observe_detectors",
    "aggregate_into_state",
]
