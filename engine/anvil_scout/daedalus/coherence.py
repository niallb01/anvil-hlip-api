"""TB-13 coherence-violation detection.

Consumes aggregated state from observability.py and emits coherence flags
that future TBs (14+) will use to drive detector adaptation. At TB-13
the flags are SURFACED but not ACTED ON — measurement only.

The three flag types are:

    HIGH_STRIP_RATE — Detector's recent strip rate exceeds threshold.
                      Detector is over-emitting ungrounded claims that
                      the Law-0 boundary has to throw out. Adaptation
                      signal: tighten detector sensitivity.

    ZERO_EVIDENCE   — Detector has fired N+ times without producing any
                      classified spans. Either the detector is broken,
                      or all inputs in this batch genuinely lacked the
                      structure it looks for. Informational at TB-13;
                      TB-14 may use it to flag detectors for inspection.

    EVIDENCE_AMP    — JB-V2-16: detector's emitted-spans count is
                      substantially higher than its unique-ranges count.
                      Many overlapping emissions for the same evidence.
                      Adaptation signal: tighten the detector or add
                      deduplication.

This module is internal observation infrastructure. It does NOT appear
in the partner-facing rationale.

Feynman: the receipts from observability.py are raw measurements; this
module is the interpreter that asks "do these measurements suggest
something is wrong?" The flags are warnings, not corrections — TB-14
is what does corrections.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


# Severity levels — sortable strings so callers can filter/sort.
SEV_INFO = "info"
SEV_WARNING = "warning"
SEV_ERROR = "error"
VALID_SEVERITIES = (SEV_INFO, SEV_WARNING, SEV_ERROR)

# Flag type identifiers — stable across TBs so future TBs can key off them.
FLAG_HIGH_STRIP_RATE = "high_strip_rate"
FLAG_ZERO_EVIDENCE = "zero_evidence"
FLAG_EVIDENCE_AMP = "evidence_amplification"


@dataclass(frozen=True)
class CoherenceFlag:
    """One flag identifying a coherence concern.

    Fields:
        flag:       One of FLAG_* constants.
        detector:   Detector name if scoped to a detector, else None.
        detail:     Short human-readable explanation citing concrete numbers.
        severity:   One of SEV_INFO, SEV_WARNING, SEV_ERROR.
        evidence:   Free-form dict of metrics supporting the flag.
    """
    flag: str
    detector: Optional[str]
    detail: str
    severity: str
    evidence: Dict[str, Any]

    def __post_init__(self) -> None:
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"CoherenceFlag severity must be one of {VALID_SEVERITIES}, "
                f"got {self.severity!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# Detection — pure functions of state
# ============================================================

# Default thresholds. Conservative — TBs 14+ may tune via state.calibration_state.
DEFAULT_HIGH_STRIP_RATE = 0.30  # 30% strip rate is concerning
DEFAULT_ZERO_EVIDENCE_MIN_CALLS = 10  # need 10+ calls before "zero evidence" is meaningful
DEFAULT_AMPLIFICATION_RATIO = 2.0  # spans_emitted / unique_ranges > 2 → over-firing


def detect_high_strip_rate(
    state: Dict[str, Any],
    threshold: float = DEFAULT_HIGH_STRIP_RATE,
) -> List[CoherenceFlag]:
    """Flag detectors whose recent strip rate exceeds threshold."""
    flags: List[CoherenceFlag] = []
    det_state = state.get("detector_state", {}) or {}
    for det_name, bucket in det_state.items():
        rate = bucket.get("recent_strip_rate", 0.0)
        if rate > threshold:
            flags.append(CoherenceFlag(
                flag=FLAG_HIGH_STRIP_RATE,
                detector=det_name,
                detail=f"recent strip rate {rate:.2%} > threshold {threshold:.2%}",
                severity=SEV_WARNING,
                evidence={
                    "recent_strip_rate": rate,
                    "threshold": threshold,
                    "calls_seen": bucket.get("calls_seen", 0),
                },
            ))
    return flags


def detect_zero_evidence(
    state: Dict[str, Any],
    min_calls: int = DEFAULT_ZERO_EVIDENCE_MIN_CALLS,
) -> List[CoherenceFlag]:
    """Flag detectors that fired N+ times without producing classified spans."""
    flags: List[CoherenceFlag] = []
    det_state = state.get("detector_state", {}) or {}
    for det_name, bucket in det_state.items():
        calls = bucket.get("calls_seen", 0)
        classified = bucket.get("spans_classified_total", 0)
        if calls >= min_calls and classified == 0:
            flags.append(CoherenceFlag(
                flag=FLAG_ZERO_EVIDENCE,
                detector=det_name,
                detail=f"{calls} calls produced 0 classified spans",
                severity=SEV_INFO,
                evidence={
                    "calls_seen": calls,
                    "spans_classified_total": classified,
                    "min_calls_threshold": min_calls,
                },
            ))
    return flags


def detect_evidence_amplification(
    state: Dict[str, Any],
    ratio_threshold: float = DEFAULT_AMPLIFICATION_RATIO,
    min_emissions: int = 10,
) -> List[CoherenceFlag]:
    """Flag detectors whose emissions-to-unique-ranges ratio is too high.

    JB-V2-16: when many overlapping spans cover the same character range,
    the detector is amplifying evidence (double-counting). The ratio of
    emitted spans to unique character ranges measures this directly.
    """
    flags: List[CoherenceFlag] = []
    det_state = state.get("detector_state", {}) or {}
    for det_name, bucket in det_state.items():
        emitted = bucket.get("spans_emitted_total", 0)
        unique = bucket.get("unique_ranges_total", 0)
        if emitted >= min_emissions and unique > 0:
            ratio = emitted / unique
            if ratio > ratio_threshold:
                flags.append(CoherenceFlag(
                    flag=FLAG_EVIDENCE_AMP,
                    detector=det_name,
                    detail=(f"spans_emitted/unique_ranges = "
                            f"{emitted}/{unique} = {ratio:.2f} "
                            f"> threshold {ratio_threshold}"),
                    severity=SEV_WARNING,
                    evidence={
                        "emitted": emitted,
                        "unique_ranges": unique,
                        "ratio": ratio,
                        "ratio_threshold": ratio_threshold,
                    },
                ))
    return flags


def detect_all_violations(state: Dict[str, Any]) -> List[CoherenceFlag]:
    """Run every detector and concatenate flags. Stable order: strip_rate,
    zero_evidence, evidence_amp. Within each detector, flags grouped together."""
    flags: List[CoherenceFlag] = []
    flags.extend(detect_high_strip_rate(state))
    flags.extend(detect_zero_evidence(state))
    flags.extend(detect_evidence_amplification(state))
    return flags


__all__ = [
    "SEV_INFO", "SEV_WARNING", "SEV_ERROR", "VALID_SEVERITIES",
    "FLAG_HIGH_STRIP_RATE", "FLAG_ZERO_EVIDENCE", "FLAG_EVIDENCE_AMP",
    "CoherenceFlag",
    "DEFAULT_HIGH_STRIP_RATE",
    "DEFAULT_ZERO_EVIDENCE_MIN_CALLS",
    "DEFAULT_AMPLIFICATION_RATIO",
    "detect_high_strip_rate",
    "detect_zero_evidence",
    "detect_evidence_amplification",
    "detect_all_violations",
]
