"""Anvil-Pantheon-Floor — grounding primitives (Packet 1).

Adapts DetMath's grounding.py pattern. A GroundingCase pairs a Scout
output with a question and a reason; a GroundingResult records what the
floor system answered. perturb_scout_output() makes a structured
perturbation of the Scout output that preserves the verified-signal SET
but bumps numeric magnitudes -- the grounding test is whether the
floor system's emission shape stays stable when the numbers change but
the verified-signal kinds don't (proves it's reasoning from the V/W/M
shape, not memorizing the specific values).

Packet 1 lands the case/result types and perturbation helpers. The
dispatcher (Clockwork Oracle) lands in Packet 10; until then,
run_grounding_cases() requires a caller-supplied dispatcher callable.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class GroundingCase:
    """A Scout output paired with a question we'll ask the floor system,
    plus the reason this case is interesting. Used by perturbation tests
    and by the basic-test admission gate."""
    name: str
    scout_output: Dict[str, Any]
    question: str = "certified emission"   # what to ask the dispatcher
    reason: str = ""


@dataclass(frozen=True)
class GroundingResult:
    """What the floor system answered. emission_present means an
    EmissionCertificate was produced; certified means it was certified
    (vs refused). pathway_audit_verified tracks the receipt's audit
    verdict. error captures exceptions (dispatcher unavailable,
    malformed input, etc.)."""
    name: str
    emission_present: bool
    certified: bool
    pathway_audit_verified: bool
    payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ─── Perturbation ─────────────────────────────────────────────────────────

def perturb_scout_output(
    scout_output: Dict[str, Any],
    *,
    numeric_delta: int = 1,
    name_suffix: str = "perturb",
) -> Dict[str, Any]:
    """Perturb the numeric content of a Scout output without changing the
    verified-signal SET (same span_kind subtypes, same V/W/M shape, same
    channel-name set). Used to test that the floor system's emission
    depends on signal SHAPE not specific values.

    Specifically:
      - bumps every integer in channel scores by `numeric_delta`
        (clamping to channel ceilings)
      - leaves verified[]/weak[]/missing[] string lists untouched
        (they are structural)
      - leaves predicted_quality and signal_density untouched
        (these are derived; perturbing them would break the chain)

    Use case: feed (original, perturbed) pairs to the dispatcher; both
    should produce certified emissions with the SAME template choice
    and the SAME slot-fill shape. If one certifies and the other doesn't,
    the grounding is on numeric coincidence, not on signal structure.
    """
    out = copy.deepcopy(scout_output)
    channel_ceilings = {
        "industry_fit": 20,
        "company_size_fit": 25,
        "decision_maker_seniority": 20,
        "budget_likelihood_score": 20,
        "growth_signals": 15,
    }
    for ch, ceiling in channel_ceilings.items():
        if ch in out and isinstance(out[ch], int):
            new = out[ch] + numeric_delta
            out[ch] = max(0, min(ceiling, new))
    # lead_score must remain consistent with channel total; recompute floor.
    if all(c in out for c in channel_ceilings):
        new_lead = sum(out[c] for c in channel_ceilings)
        out["lead_score"] = max(0, min(100, new_lead))
    out["_perturbation_marker"] = name_suffix
    return out


# ─── Dispatcher protocol + runner ─────────────────────────────────────────

Dispatcher = Callable[[Dict[str, Any], str], Dict[str, Any]]
"""A dispatcher takes (scout_output, question) and returns a dict with
at least the keys:
  emission_present:        bool
  certified:               bool
  pathway_audit_verified:  bool
  payload:                 dict | None
The Clockwork Oracle (Packet 10) implements this protocol. Until then,
tests supply a stub."""


def run_grounding_cases(
    cases: Sequence[GroundingCase],
    dispatcher: Optional[Dispatcher] = None,
) -> List[GroundingResult]:
    """Run each case through the dispatcher and collect results.
    If dispatcher is None, returns results with error='no dispatcher
    available'; this is the Packet 1 state (dispatcher lands in Packet 10).
    """
    out: List[GroundingResult] = []
    for case in cases:
        if dispatcher is None:
            out.append(GroundingResult(
                name=case.name,
                emission_present=False,
                certified=False,
                pathway_audit_verified=False,
                error="no dispatcher available (Clockwork Oracle lands in Packet 10)",
            ))
            continue
        try:
            r = dispatcher(case.scout_output, case.question)
            out.append(GroundingResult(
                name=case.name,
                emission_present=bool(r.get("emission_present", False)),
                certified=bool(r.get("certified", False)),
                pathway_audit_verified=bool(r.get("pathway_audit_verified", False)),
                payload=r.get("payload"),
            ))
        except Exception as exc:  # defensive: a dispatcher crash is itself a result
            out.append(GroundingResult(
                name=case.name,
                emission_present=False,
                certified=False,
                pathway_audit_verified=False,
                error=str(exc),
            ))
    return out
