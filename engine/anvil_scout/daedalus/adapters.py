"""TB-14 detector adapters — promotes detectors from sub-T0 to T1.

Each detector gets ONE bounded scalar adapter parameter: `min_span_length`.
Spans shorter than the adapter's threshold are filtered before the
classifier sees them. The threshold updates each LEARNING-mode call based
on coherence flags from TB-13.

Why this works as a Law III T1 implementation
---------------------------------------------
Each detector becomes a loop with:
  - perceive  : reads input text + current adapter from state
  - predict   : emits spans (then filters by adapter)
  - act       : filtered spans flow into classifier
  - observe   : downstream Law-0 strip rate becomes the coherence signal
  - update    : adapter increments on HIGH_STRIP_RATE, decays on calm calls

The update is a pure function of (current_value, flag, calls_since_change).
No randomness, no time-dependence. Deterministic given history → Law I.
Bounded [0, 50] → Law 0. Updates on observation → Law III. Full
perceive→predict→act→observe cycle → Agentic Corollary.

Per JB-V2-13: adapters store integers only — no text, no PII.
Per JB-V2-14: in SNAPSHOT mode adapters are READ but NEVER written.
Per JB-V2-18 (pay rent): TB-14 must produce measurable behavioral change.
   Verified by adapters_diverge_under_pressure test.

Feynman: each detector has one knob. When the boundary keeps throwing out
this detector's spans, we turn its knob to require longer spans (filter
the over-eager short matches). When things calm down, the knob slowly
turns back toward "let everything through." The knob has a max value so
it can't lock the detector out entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ============================================================
# Constants — tuned conservatively
# ============================================================

# Adapter parameter bound (Law 0 at T1).
MIN_SPAN_LENGTH_FLOOR = 0
MIN_SPAN_LENGTH_CEIL = 50

# Update step size — small, so adaptation is gradual + observable.
INCREMENT_STEP = 1
DECAY_STEP = 1

# How many calm calls before we decay one step toward baseline.
DECAY_AFTER_CALM_CALLS = 5

# The four detector names — kept in sync with observability.DETECTOR_NAMES
DETECTOR_NAMES: Tuple[str, ...] = ("testimony", "quantity", "causal", "missing_phrase")


# ============================================================
# Adapter shape
# ============================================================

@dataclass(frozen=True)
class DetectorAdapter:
    """One scalar adapter per detector.

    Fields:
        detector_name:        Identifier — one of DETECTOR_NAMES.
        min_span_length:      Spans shorter than this are filtered out
                              before the classifier sees them. Bounded
                              [0, 50]. Default 0 = no filtering.
        calls_since_change:   How many LEARNING-mode calls since the
                              last update. Used by the decay rule.
        last_update_reason:   Short string describing the most recent
                              change ("init" / "high_strip_rate" / "decay").
                              Diagnostic only.
    """
    detector_name: str
    min_span_length: int = 0
    calls_since_change: int = 0
    last_update_reason: str = "init"

    def __post_init__(self) -> None:
        if self.detector_name not in DETECTOR_NAMES:
            raise ValueError(
                f"unknown detector_name {self.detector_name!r}; "
                f"valid: {DETECTOR_NAMES}"
            )
        if not (MIN_SPAN_LENGTH_FLOOR <= self.min_span_length <= MIN_SPAN_LENGTH_CEIL):
            raise ValueError(
                f"min_span_length must be in "
                f"[{MIN_SPAN_LENGTH_FLOOR}, {MIN_SPAN_LENGTH_CEIL}], "
                f"got {self.min_span_length}"
            )
        if self.calls_since_change < 0:
            raise ValueError("calls_since_change must be ≥ 0")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def default_adapter(detector_name: str) -> DetectorAdapter:
    """Fresh adapter for one detector — zero-impact baseline (Law 0 default).

    The default min_span_length=0 means no spans are filtered, so default
    adapters preserve v0.1.0 partner-facing behavior exactly. JB-14-1.
    """
    return DetectorAdapter(detector_name=detector_name)


def default_adapters() -> Dict[str, DetectorAdapter]:
    """One default adapter per detector, keyed by name."""
    return {name: default_adapter(name) for name in DETECTOR_NAMES}


# ============================================================
# Serialization — adapter state lives in state["detector_state"][name]["adapter"]
# ============================================================

def adapter_from_dict(detector_name: str, d: Optional[Dict[str, Any]]) -> DetectorAdapter:
    """Reconstruct an adapter from a state-dict entry.

    JB-14-4: if `d` is None or missing keys, fall back to default. This
    lets v2 read state files written by TB-13 (which had no adapter key)
    without crashing.
    """
    if not isinstance(d, dict):
        return default_adapter(detector_name)
    try:
        return DetectorAdapter(
            detector_name=detector_name,
            min_span_length=int(d.get("min_span_length", 0)),
            calls_since_change=int(d.get("calls_since_change", 0)),
            last_update_reason=str(d.get("last_update_reason", "init")),
        )
    except (ValueError, TypeError):
        # Corrupted entry — fall back to baseline
        return default_adapter(detector_name)


def adapters_from_state(state: Dict[str, Any]) -> Dict[str, DetectorAdapter]:
    """Pull adapters out of a state dict. Missing detectors → defaults."""
    det_state = state.get("detector_state", {}) or {}
    out: Dict[str, DetectorAdapter] = {}
    for name in DETECTOR_NAMES:
        bucket = det_state.get(name, {}) or {}
        adapter_subkey = bucket.get("adapter") if isinstance(bucket, dict) else None
        out[name] = adapter_from_dict(name, adapter_subkey)
    return out


def adapters_to_state(state: Dict[str, Any], adapters: Dict[str, DetectorAdapter]) -> None:
    """Write adapters back into state in-place.

    Uses the TB-13 bucket shape — adapter sits under
    state["detector_state"][name]["adapter"] so TB-13 observability counters
    in the same bucket are preserved untouched.
    """
    if "detector_state" not in state or not isinstance(state["detector_state"], dict):
        state["detector_state"] = {}
    for name, adapter in adapters.items():
        bucket = state["detector_state"].setdefault(name, {})
        if not isinstance(bucket, dict):
            bucket = {}
            state["detector_state"][name] = bucket
        bucket["adapter"] = adapter.to_dict()


# ============================================================
# Update rule — Law III at T1
# ============================================================

def update_adapter(
    current: DetectorAdapter,
    high_strip_rate_flagged: bool,
) -> DetectorAdapter:
    """Pure update function: (current_adapter, flag) → next_adapter.

    Rules:
        1. If HIGH_STRIP_RATE flag is set for this detector: increment
           min_span_length by 1 (clamped to ceiling). Reason: "high_strip_rate".
           calls_since_change reset to 0.
        2. Else if calls_since_change >= DECAY_AFTER_CALM_CALLS and
           min_span_length > 0: decrement by 1 (clamped to floor).
           Reason: "decay". calls_since_change reset to 0.
        3. Else: no change to min_span_length; calls_since_change += 1.

    Pure function — no time, no randomness. Same inputs → same outputs
    (Law I at T1). Bounded outputs (Law 0 at T1). Updates on observation
    (Law III at T1).
    """
    if high_strip_rate_flagged:
        new_val = min(current.min_span_length + INCREMENT_STEP, MIN_SPAN_LENGTH_CEIL)
        if new_val == current.min_span_length:
            # Already at ceiling — no change but still record the attempt
            return replace(
                current,
                calls_since_change=0,
                last_update_reason="high_strip_rate_at_ceiling",
            )
        return replace(
            current,
            min_span_length=new_val,
            calls_since_change=0,
            last_update_reason="high_strip_rate",
        )

    if (current.calls_since_change + 1 >= DECAY_AFTER_CALM_CALLS
            and current.min_span_length > MIN_SPAN_LENGTH_FLOOR):
        new_val = max(current.min_span_length - DECAY_STEP, MIN_SPAN_LENGTH_FLOOR)
        return replace(
            current,
            min_span_length=new_val,
            calls_since_change=0,
            last_update_reason="decay",
        )

    return replace(
        current,
        calls_since_change=current.calls_since_change + 1,
    )


def update_all_adapters(
    adapters: Dict[str, DetectorAdapter],
    flagged_detectors: Iterable[str],
) -> Dict[str, DetectorAdapter]:
    """Apply update_adapter to each adapter; flagged_detectors lists
    detectors that hit HIGH_STRIP_RATE this call.

    Returns a NEW dict — adapters is treated as immutable input.
    """
    flagged_set = set(flagged_detectors)
    return {
        name: update_adapter(adapter, name in flagged_set)
        for name, adapter in adapters.items()
    }


# ============================================================
# Span filtering — adapter applied to detector output
# ============================================================

def apply_adapters_to_spans(
    spans: List,
    adapters: Dict[str, DetectorAdapter],
) -> List:
    """Filter spans by per-detector min_span_length adapter.

    Spans whose text length < adapter[detector].min_span_length are removed.
    With default adapters (all min_span_length=0) this is a no-op — every
    span passes (JB-14-1 zero-impact verified).

    Args:
        spans: List of Span objects from run_all_detectors. Each must have
               .kind (str) and .text (str) attributes.
        adapters: Dict mapping detector_name → DetectorAdapter.

    Returns:
        Filtered list of spans.
    """
    # Map span.kind -> detector_name. The observability module uses
    # the same mapping (kind "missing" -> detector "missing_phrase").
    KIND_TO_DETECTOR = {
        "testimony": "testimony",
        "quantity": "quantity",
        "causal": "causal",
        "missing": "missing_phrase",
    }

    out = []
    for span in spans:
        kind = getattr(span, "kind", None)
        text = getattr(span, "text", "")
        det_name = KIND_TO_DETECTOR.get(kind)
        if det_name is None:
            # Unknown kind — pass through unchanged (strip-don't-raise)
            out.append(span)
            continue
        adapter = adapters.get(det_name)
        if adapter is None or adapter.min_span_length <= 0:
            out.append(span)
            continue
        if len(text) >= adapter.min_span_length:
            out.append(span)
        # else: filtered out
    return out


# ============================================================
# Public surface
# ============================================================

__all__ = [
    "DETECTOR_NAMES",
    "MIN_SPAN_LENGTH_FLOOR",
    "MIN_SPAN_LENGTH_CEIL",
    "DECAY_AFTER_CALM_CALLS",
    "DetectorAdapter",
    "default_adapter",
    "default_adapters",
    "adapter_from_dict",
    "adapters_from_state",
    "adapters_to_state",
    "update_adapter",
    "update_all_adapters",
    "apply_adapters_to_spans",
]
