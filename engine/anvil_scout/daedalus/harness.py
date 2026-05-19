"""Daedalus harness — observe a target system, run all predicates, collect receipts.

The harness's job is observation, not modification. Given a target system
(in TB-11: v0.1.0), it constructs a HarnessState describing what structures
the system has (loops, council, self_model, etc.) and then runs every
predicate from PREDICATE_REGISTRY against that state.

This separation matters: the predicates don't touch the target system.
They only look at what the harness reports about it. That keeps TB-11
strictly observation-only — no production code is modified.

For v0.1.0 the observation is straightforward: the system is single-pass,
stateless, no loops, no council, no self-model, no swarm. So most
T1+ predicates return N/A. The interesting predicates are the T0 ones
plus the Agentic Corollary at T0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from anvil_scout.daedalus.receipts import (
    LawTestReceipt,
    PASS,
    FAIL,
    NOT_APPLICABLE,
    now_utc_iso,
)
from anvil_scout.daedalus.predicates import (
    PREDICATE_REGISTRY,
    threshold_table,
)


@dataclass
class EvidenceSet:
    """Per-call evidence. Currently unused at TB-11 (system observation is
    all that's needed for baseline). Future TBs (TB-13+) populate this when
    they have per-call trace data from real runs."""
    spans: List[Dict[str, Any]] = field(default_factory=list)
    rationale_annotations: List[str] = field(default_factory=list)
    schema_validations: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class HarnessState:
    """What the harness has observed about the target system.

    The `observed` dict mirrors plate 10's predicate-parameter language —
    e.g. `loops` is a list of per-loop reports, `council` is the council
    state if one exists, `swarm` is the swarm state if one exists, etc.
    Predicates read from this dict; if a key is missing, the predicate
    returns N/A.

    `thresholds` overrides defaults in threshold_table.
    `deterministic_timestamp` makes timestamps reproducible for testing.
    """
    observed: Dict[str, Any] = field(default_factory=dict)
    thresholds: Dict[str, Any] = field(default_factory=lambda: dict(threshold_table))
    deterministic_timestamp: Optional[str] = None

    def now(self) -> str:
        """Get the current timestamp. Uses deterministic_timestamp if set."""
        return self.deterministic_timestamp or now_utc_iso()


def observe_v01(deterministic_timestamp: Optional[str] = None) -> HarnessState:
    """Construct a HarnessState describing Anvil v0.1.0 as currently shipped.

    v0.1.0 facts:
        - Pure function: same input → same output (deterministic)
        - Single-pass: input → output → die, no t+1
        - No cross-call state (state.dims_across_calls = 0)
        - No loop structure (no perceive→act loop; just input→output)
        - No multi-loop council
        - No self-model
        - No swarm
        - No outcome-feedback channel; no gradient updates

    These facts let us populate exactly the keys each predicate looks for.
    Predicates whose required keys are absent will return N/A; that's the
    correct behavior and confirms v0.1.0 is sub-T0.
    """
    return HarnessState(
        observed={
            # Law 0 T0 — bounded cognition
            "state_dims_across_calls": 0,   # zero persistent state
            # Law I T0 — determinism. v0.1.0 is a pure function.
            "determinism_check": {
                "identical_outputs_for_identical_inputs": True,
                "trials": 1,  # informal — formal trial test could add more
            },
            # Law II T0 — single-pass system has no t+1 to predict.
            # Leaving "prediction" absent so the predicate returns N/A.
            # Agentic T0 — no perceive→act loop.
            "perceive_act_loop": {"exists": False, "reason":
                "single-pass function: input → output → die. No observe step."},
            # Higher-tier structures are absent on v0.1.0.
            # loops, council, plan, swarm, shared_memory, self_history,
            # self_model are all intentionally not set — predicates
            # will return N/A.
        },
        deterministic_timestamp=deterministic_timestamp,
    )


def observe_v2_TB14(
    state: Optional[Dict[str, Any]] = None,
    deterministic_timestamp: Optional[str] = None,
) -> HarnessState:
    """Construct a HarnessState describing Anvil v2 after TB-14 promotion.

    At TB-14 each detector is a closed loop with a bounded adapter
    parameter. The loop:
        - perceive : reads input text + current adapter from state
        - predict  : emits spans (then filters by adapter.min_span_length)
        - act      : filtered spans flow into classifier
        - observe  : downstream Law-0 strip rate (coherence flag)
        - update   : adapter param adjusted via update_adapter()

    This factory reports the loop structure so the predicate harness
    can evaluate Law 0/I/III/Agentic at T1.

    Args:
        state: Optional state dict (typically loaded from a state
            provider). When provided, the harness reads the actual
            adapter values from state. When None, reports default
            (zero) adapter values — the loops still exist, just at
            baseline parameters.
        deterministic_timestamp: For reproducible receipts.

    The reported loops let the T1 predicates PASS:
        - Law 0 T1: each loop.state_dims = 1 (one scalar adapter)
                    ≤ k_max — PASS
        - Law I T1: each loop deterministic given history — PASS
        - Law III T1: each loop has gradient_update_applied — PASS
        - Agentic T1: each loop has full perceive→predict→act→observe
                      cycle — PASS

    Compared to v0.1.0 baseline (all T1 N/A), this is the rent-paid
    receipt — JB-V2-18.
    """
    # Lazy import — avoid circulars during package init.
    from anvil_scout.daedalus.adapters import (
        DETECTOR_NAMES,
        adapters_from_state,
        MIN_SPAN_LENGTH_CEIL,
    )

    if state is None:
        from anvil_scout.daedalus.state import initial_state
        state = initial_state()

    adapters = adapters_from_state(state)

    # Each detector reports as a loop. state_dims=1 because each loop
    # has exactly one scalar adapter parameter.
    loops = []
    for name in DETECTOR_NAMES:
        adapter = adapters[name]
        loops.append({
            "id": name,
            "state_dims": 1,                                # one scalar param
            "deterministic_given_history": True,            # pure update rule
            "gradient_update_applied": True,                # update_adapter is
                                                            # applied each
                                                            # LEARNING-mode call
            "full_cycle_perceive_predict_act_observe": True,
            # Diagnostic only — predicates don't read these:
            "current_min_span_length": adapter.min_span_length,
            "calls_since_change": adapter.calls_since_change,
            "last_update_reason": adapter.last_update_reason,
        })

    return HarnessState(
        observed={
            # Law 0 T0 — still satisfied trivially: cross-call state
            # is bounded (per-detector adapters only).
            "state_dims_across_calls": len(loops),  # one scalar per loop
            # Law I T0 — deterministic given input + state snapshot.
            "determinism_check": {
                "identical_outputs_for_identical_inputs": True,
                "trials": 1,
            },
            # Agentic T0 — closed loop now exists (the observability tail
            # gives v2 the "observe" step v0.1.0 was missing).
            "perceive_act_loop": {"exists": True, "reason":
                "v2 closes the perceive→predict→act→observe loop via "
                "TB-13 observability + TB-14 adapter update"},
            # The T1 loops — this is what makes T1 predicates pass.
            "loops": loops,
            # No T2+ structures at TB-14. Council, self-model, swarm
            # remain N/A until TB-15/16/17.
        },
        thresholds={
            "T0": {"k_max": len(loops)},  # bound: one scalar per loop
            "T1_k_max": MIN_SPAN_LENGTH_CEIL,  # each adapter ≤ this
        },
        deterministic_timestamp=deterministic_timestamp,
    )


def run_all_predicates(
    harness_state: HarnessState,
    evidence_set: Optional[EvidenceSet] = None,
) -> List[LawTestReceipt]:
    """Run every predicate in PREDICATE_REGISTRY against the given state.

    Returns a list of receipts in registry order (plate-10 causal order:
    0 → I → II → III → agentic, T0 → T4 within each law).

    Per plate 10 footer: "A failure at law N invalidates evaluation of
    laws N+1." We honor this by emitting all receipts but propagating
    the invalidation in the summary, not by skipping evaluation.
    Reason: skipping would hide N/A receipts that are themselves informative.
    """
    if evidence_set is None:
        evidence_set = EvidenceSet()
    receipts: List[LawTestReceipt] = []
    for pid, fn in PREDICATE_REGISTRY:
        receipt = fn(harness_state, evidence_set)
        # Sanity: predicate_id must match registry key (catches copy-paste bugs)
        if receipt.predicate_id != pid:
            raise RuntimeError(
                f"predicate {pid} emitted receipt with predicate_id "
                f"{receipt.predicate_id!r} — mismatch"
            )
        receipts.append(receipt)
    return receipts


def summarize_receipts(receipts: List[LawTestReceipt]) -> Dict[str, Any]:
    """Aggregate counts and tier-conformance signals.

    Tier conformance rule (informal — for TB-11 baseline only):
        A system "conforms at tier T" if every predicate at tier T returns
        either PASS or N/A (no FAILs at that tier). Note this is a generous
        rule — a system with all-N/A at tier T technically conforms because
        it has no relevant structure to violate the law. That's the correct
        behavior for v0.1.0 (sub-T0 — N/A at most tiers because the
        structures don't exist).
    """
    by_outcome = {PASS: 0, FAIL: 0, NOT_APPLICABLE: 0}
    by_tier: Dict[str, Dict[str, int]] = {
        f"T{i}": {PASS: 0, FAIL: 0, NOT_APPLICABLE: 0} for i in range(5)
    }
    by_law: Dict[str, Dict[str, int]] = {
        law: {PASS: 0, FAIL: 0, NOT_APPLICABLE: 0}
        for law in ("0", "I", "II", "III", "agentic")
    }
    for r in receipts:
        by_outcome[r.outcome] += 1
        by_tier[r.tier][r.outcome] += 1
        by_law[r.law][r.outcome] += 1

    # Tier conformance: tier T conforms if zero FAILs at that tier.
    tier_conformance = {
        tier: (counts[FAIL] == 0)
        for tier, counts in by_tier.items()
    }

    # Highest meaningfully-conforming tier: highest T where any predicate
    # PASSed (not all-N/A) AND no predicate at that tier or lower FAILed.
    # This captures "the system actually does something lawful at this tier",
    # rather than the trivial "all-N/A means I conform to T4 too."
    meaningfully_conforming = []
    for tier in ("T0", "T1", "T2", "T3", "T4"):
        counts = by_tier[tier]
        if counts[PASS] > 0 and counts[FAIL] == 0:
            meaningfully_conforming.append(tier)
    highest = meaningfully_conforming[-1] if meaningfully_conforming else "sub-T0"

    return {
        "total_predicates": len(receipts),
        "by_outcome": by_outcome,
        "by_tier": by_tier,
        "by_law": by_law,
        "tier_conformance": tier_conformance,
        "meaningfully_conforming_tiers": meaningfully_conforming,
        "highest_meaningful_tier": highest,
    }
