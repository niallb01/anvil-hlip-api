"""Daedalus law predicates — plate 10 compiled to Python.

Five laws × five tiers = 25 cells. Law III at T0 is trivial-true by design,
so 24 active predicates. Each predicate function:

    1. Takes (harness_state, evidence_set)
    2. Decides applicability — is the target system structurally capable
       of being tested at this tier? If not, returns N/A.
    3. Evaluates the predicate body.
    4. Returns a LawTestReceipt.

Predicate IDs follow the pattern law{law}_T{tier} so receipts can be diffed
across runs and across systems (e.g. v0.1.0 vs v2 at TB-15).

Feynman: think of each predicate as a tiny test. The test asks one specific
question about one specific tier ("does this system update its parameters
after each loop's outcome?") and emits a receipt saying yes/no/can't-tell
with the supporting observation.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from anvil_scout.daedalus.receipts import (
    LawTestReceipt,
    PASS,
    FAIL,
    NOT_APPLICABLE,
)


# Tier-dependent ε thresholds (plate 10 footer references this table).
# Conservative starting values; later TBs may tune them.
threshold_table = {
    "T1": {"epsilon_loop": 0.1},          # Law II at T1
    "T2": {"epsilon_integrated": 0.15},   # Law II at T2
    "T3": {"epsilon_3": 0.2, "tier_horizon": 10},  # Law II / Law 0 at T3
    "T4": {"epsilon_4": 0.25, "swarm_size_max": 16},  # Law II / Law 0 at T4
    "T0": {"k_max": 1},                   # Law 0 at T0 — strict single dim
    # Note: T1 Law 0 also uses k_max; default to a generous 128 — overridable
    # by HarnessState.threshold_overrides.
    "T1_k_max": 128,
}


# ============================================================
# Law 0 — Bounded cognition
# ============================================================

def law0_T0(harness_state, evidence_set) -> LawTestReceipt:
    """state.dims ≤ 1 (strict single-dim state).

    For v0.1.0: state.dims is conceptually the dimension of cross-call state.
    v0.1.0 has zero persistent state across calls, so state.dims = 0 ≤ 1.
    PASS trivially.
    """
    state_dims = harness_state.observed.get("state_dims_across_calls", None)
    if state_dims is None:
        return LawTestReceipt(
            law="0", tier="T0", outcome=NOT_APPLICABLE,
            detail="harness has not reported state_dims_across_calls",
            predicate_id="law0_T0",
            timestamp_utc=harness_state.now(),
            evidence={"missing_key": "state_dims_across_calls"},
        )
    k_max = harness_state.thresholds.get("T0", {}).get("k_max", 1)
    if state_dims <= k_max:
        return LawTestReceipt(
            law="0", tier="T0", outcome=PASS,
            detail=f"state.dims={state_dims} ≤ k_max={k_max}",
            predicate_id="law0_T0",
            timestamp_utc=harness_state.now(),
            evidence={"state_dims": state_dims, "k_max": k_max},
        )
    return LawTestReceipt(
        law="0", tier="T0", outcome=FAIL,
        detail=f"state.dims={state_dims} > k_max={k_max} — bounded cognition violated",
        predicate_id="law0_T0",
        timestamp_utc=harness_state.now(),
        evidence={"state_dims": state_dims, "k_max": k_max},
    )


def law0_T1(harness_state, evidence_set) -> LawTestReceipt:
    """∀ loop: loop.state.dims ≤ k_max."""
    loops = harness_state.observed.get("loops", None)
    if not loops:
        return LawTestReceipt(
            law="0", tier="T1", outcome=NOT_APPLICABLE,
            detail="no loop structure observed — system is single-pass",
            predicate_id="law0_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": 0},
        )
    k_max = harness_state.thresholds.get("T1_k_max", 128)
    bad = [lp for lp in loops if lp.get("state_dims", 0) > k_max]
    if not bad:
        return LawTestReceipt(
            law="0", tier="T1", outcome=PASS,
            detail=f"all {len(loops)} loops have state.dims ≤ {k_max}",
            predicate_id="law0_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": len(loops), "k_max": k_max},
        )
    return LawTestReceipt(
        law="0", tier="T1", outcome=FAIL,
        detail=f"{len(bad)} loops exceed k_max={k_max}",
        predicate_id="law0_T1",
        timestamp_utc=harness_state.now(),
        evidence={"violating_loops": [lp.get("id") for lp in bad]},
    )


def law0_T2(harness_state, evidence_set) -> LawTestReceipt:
    """sum(loop.budget) ≤ shared_budget."""
    council = harness_state.observed.get("council", None)
    if not council:
        return LawTestReceipt(
            law="0", tier="T2", outcome=NOT_APPLICABLE,
            detail="no council structure observed — no shared-budget concept exists",
            predicate_id="law0_T2",
            timestamp_utc=harness_state.now(),
            evidence={"council_present": False},
        )
    budgets = council.get("loop_budgets", [])
    shared = council.get("shared_budget", None)
    if shared is None:
        return LawTestReceipt(
            law="0", tier="T2", outcome=NOT_APPLICABLE,
            detail="council present but no shared_budget set",
            predicate_id="law0_T2",
            timestamp_utc=harness_state.now(),
            evidence={"council_present": True, "shared_budget_set": False},
        )
    total = sum(budgets)
    if total <= shared:
        return LawTestReceipt(
            law="0", tier="T2", outcome=PASS,
            detail=f"sum(budgets)={total} ≤ shared_budget={shared}",
            predicate_id="law0_T2",
            timestamp_utc=harness_state.now(),
            evidence={"sum_budgets": total, "shared_budget": shared},
        )
    return LawTestReceipt(
        law="0", tier="T2", outcome=FAIL,
        detail=f"sum(budgets)={total} > shared_budget={shared} — council budget violated",
        predicate_id="law0_T2",
        timestamp_utc=harness_state.now(),
        evidence={"sum_budgets": total, "shared_budget": shared},
    )


def law0_T3(harness_state, evidence_set) -> LawTestReceipt:
    """plan_horizon ≤ tier_horizon[3]."""
    plan = harness_state.observed.get("plan", None)
    if not plan:
        return LawTestReceipt(
            law="0", tier="T3", outcome=NOT_APPLICABLE,
            detail="no plan structure observed — system has no future-planning component",
            predicate_id="law0_T3",
            timestamp_utc=harness_state.now(),
            evidence={"plan_present": False},
        )
    horizon = plan.get("horizon", None)
    bound = harness_state.thresholds.get("T3", {}).get("tier_horizon", 10)
    if horizon is None:
        return LawTestReceipt(
            law="0", tier="T3", outcome=NOT_APPLICABLE,
            detail="plan present but no horizon set",
            predicate_id="law0_T3",
            timestamp_utc=harness_state.now(),
            evidence={"plan_present": True, "horizon_set": False},
        )
    if horizon <= bound:
        return LawTestReceipt(
            law="0", tier="T3", outcome=PASS,
            detail=f"plan_horizon={horizon} ≤ tier_horizon[3]={bound}",
            predicate_id="law0_T3",
            timestamp_utc=harness_state.now(),
            evidence={"horizon": horizon, "bound": bound},
        )
    return LawTestReceipt(
        law="0", tier="T3", outcome=FAIL,
        detail=f"plan_horizon={horizon} > tier_horizon[3]={bound}",
        predicate_id="law0_T3",
        timestamp_utc=harness_state.now(),
        evidence={"horizon": horizon, "bound": bound},
    )


def law0_T4(harness_state, evidence_set) -> LawTestReceipt:
    """|peer_models| ≤ swarm_size_max."""
    swarm = harness_state.observed.get("swarm", None)
    if not swarm:
        return LawTestReceipt(
            law="0", tier="T4", outcome=NOT_APPLICABLE,
            detail="no swarm structure observed — system is not a conductor",
            predicate_id="law0_T4",
            timestamp_utc=harness_state.now(),
            evidence={"swarm_present": False},
        )
    peers = swarm.get("peer_models", [])
    bound = harness_state.thresholds.get("T4", {}).get("swarm_size_max", 16)
    n = len(peers)
    if n <= bound:
        return LawTestReceipt(
            law="0", tier="T4", outcome=PASS,
            detail=f"|peer_models|={n} ≤ swarm_size_max={bound}",
            predicate_id="law0_T4",
            timestamp_utc=harness_state.now(),
            evidence={"peer_count": n, "bound": bound},
        )
    return LawTestReceipt(
        law="0", tier="T4", outcome=FAIL,
        detail=f"|peer_models|={n} > swarm_size_max={bound}",
        predicate_id="law0_T4",
        timestamp_utc=harness_state.now(),
        evidence={"peer_count": n, "bound": bound},
    )


# ============================================================
# Law I — Persistence (deterministic state evolution)
# ============================================================

def law1_T0(harness_state, evidence_set) -> LawTestReceipt:
    """state(t+1) deterministic given (state(t), input).

    For a pure function: same input always produces same output.
    The harness reports whether a duplicate-input run was performed.
    """
    determinism = harness_state.observed.get("determinism_check", None)
    if determinism is None:
        return LawTestReceipt(
            law="I", tier="T0", outcome=NOT_APPLICABLE,
            detail="harness did not run determinism_check",
            predicate_id="law1_T0",
            timestamp_utc=harness_state.now(),
            evidence={"missing_key": "determinism_check"},
        )
    if determinism.get("identical_outputs_for_identical_inputs") is True:
        return LawTestReceipt(
            law="I", tier="T0", outcome=PASS,
            detail=f"identical inputs produced byte-identical outputs across "
                   f"{determinism.get('trials', '?')} trials",
            predicate_id="law1_T0",
            timestamp_utc=harness_state.now(),
            evidence={"trials": determinism.get("trials")},
        )
    return LawTestReceipt(
        law="I", tier="T0", outcome=FAIL,
        detail="identical inputs produced different outputs — determinism violated",
        predicate_id="law1_T0",
        timestamp_utc=harness_state.now(),
        evidence=determinism,
    )


def law1_T1(harness_state, evidence_set) -> LawTestReceipt:
    """∀ loop: state(t+1) deterministic given loop.history."""
    loops = harness_state.observed.get("loops", None)
    if not loops:
        return LawTestReceipt(
            law="I", tier="T1", outcome=NOT_APPLICABLE,
            detail="no loop structure observed",
            predicate_id="law1_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": 0},
        )
    non_det = [lp for lp in loops if not lp.get("deterministic_given_history", False)]
    if not non_det:
        return LawTestReceipt(
            law="I", tier="T1", outcome=PASS,
            detail=f"all {len(loops)} loops deterministic given history",
            predicate_id="law1_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": len(loops)},
        )
    return LawTestReceipt(
        law="I", tier="T1", outcome=FAIL,
        detail=f"{len(non_det)} loops non-deterministic given history",
        predicate_id="law1_T1",
        timestamp_utc=harness_state.now(),
        evidence={"violating_loops": [lp.get("id") for lp in non_det]},
    )


def law1_T2(harness_state, evidence_set) -> LawTestReceipt:
    """shared_memory.read(t+ε) = shared_memory.write(t) for ε < window."""
    sm = harness_state.observed.get("shared_memory", None)
    if not sm:
        return LawTestReceipt(
            law="I", tier="T2", outcome=NOT_APPLICABLE,
            detail="no shared_memory observed — system has no council layer",
            predicate_id="law1_T2",
            timestamp_utc=harness_state.now(),
            evidence={"shared_memory_present": False},
        )
    consistent = sm.get("read_after_write_consistent_within_window")
    if consistent is True:
        return LawTestReceipt(
            law="I", tier="T2", outcome=PASS,
            detail="shared_memory read-after-write consistent within window",
            predicate_id="law1_T2",
            timestamp_utc=harness_state.now(),
            evidence=sm,
        )
    return LawTestReceipt(
        law="I", tier="T2", outcome=FAIL,
        detail="shared_memory read-after-write inconsistent",
        predicate_id="law1_T2",
        timestamp_utc=harness_state.now(),
        evidence=sm,
    )


def law1_T3(harness_state, evidence_set) -> LawTestReceipt:
    """self_history.replay(t→t+Δ) = self_predict(history, t→t+Δ)."""
    sh = harness_state.observed.get("self_history", None)
    if not sh:
        return LawTestReceipt(
            law="I", tier="T3", outcome=NOT_APPLICABLE,
            detail="no self_history observed — system has no self-model",
            predicate_id="law1_T3",
            timestamp_utc=harness_state.now(),
            evidence={"self_history_present": False},
        )
    if sh.get("replay_matches_self_predict") is True:
        return LawTestReceipt(
            law="I", tier="T3", outcome=PASS,
            detail="self_history.replay matches self_predict",
            predicate_id="law1_T3",
            timestamp_utc=harness_state.now(),
            evidence=sh,
        )
    return LawTestReceipt(
        law="I", tier="T3", outcome=FAIL,
        detail="self_history.replay diverges from self_predict",
        predicate_id="law1_T3",
        timestamp_utc=harness_state.now(),
        evidence=sh,
    )


def law1_T4(harness_state, evidence_set) -> LawTestReceipt:
    """peer_histories.consistent_across_swarm()."""
    swarm = harness_state.observed.get("swarm", None)
    if not swarm:
        return LawTestReceipt(
            law="I", tier="T4", outcome=NOT_APPLICABLE,
            detail="no swarm — peer_histories not applicable",
            predicate_id="law1_T4",
            timestamp_utc=harness_state.now(),
            evidence={"swarm_present": False},
        )
    if swarm.get("peer_histories_consistent") is True:
        return LawTestReceipt(
            law="I", tier="T4", outcome=PASS,
            detail="peer_histories consistent across swarm",
            predicate_id="law1_T4",
            timestamp_utc=harness_state.now(),
            evidence=swarm,
        )
    return LawTestReceipt(
        law="I", tier="T4", outcome=FAIL,
        detail="peer_histories inconsistent across swarm",
        predicate_id="law1_T4",
        timestamp_utc=harness_state.now(),
        evidence=swarm,
    )


# ============================================================
# Law II — Prediction (bounded prediction error per tier)
# ============================================================

def law2_T0(harness_state, evidence_set) -> LawTestReceipt:
    """pred(t+1) = f(state(t)) (zero error).

    A T0 thermostat must predict its next state with zero error (the
    setpoint comparison IS the prediction). For v0.1.0 (single-pass with
    no notion of t+1), this predicate is N/A — there is no t+1.
    """
    pred = harness_state.observed.get("prediction", None)
    if pred is None:
        return LawTestReceipt(
            law="II", tier="T0", outcome=NOT_APPLICABLE,
            detail="no prediction structure — single-pass system has no t+1",
            predicate_id="law2_T0",
            timestamp_utc=harness_state.now(),
            evidence={"prediction_present": False},
        )
    error = pred.get("error_at_T0", None)
    if error == 0:
        return LawTestReceipt(
            law="II", tier="T0", outcome=PASS,
            detail="T0 prediction error = 0",
            predicate_id="law2_T0",
            timestamp_utc=harness_state.now(),
            evidence={"error": error},
        )
    return LawTestReceipt(
        law="II", tier="T0", outcome=FAIL,
        detail=f"T0 prediction error = {error}, must be zero",
        predicate_id="law2_T0",
        timestamp_utc=harness_state.now(),
        evidence={"error": error},
    )


def law2_T1(harness_state, evidence_set) -> LawTestReceipt:
    """∀ loop: ‖pred − actual‖₂ ≤ ε_loop."""
    loops = harness_state.observed.get("loops", None)
    if not loops:
        return LawTestReceipt(
            law="II", tier="T1", outcome=NOT_APPLICABLE,
            detail="no loops observed",
            predicate_id="law2_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": 0},
        )
    eps = harness_state.thresholds.get("T1", {}).get("epsilon_loop", 0.1)
    over = [lp for lp in loops
            if lp.get("prediction_error_l2") is not None
            and lp["prediction_error_l2"] > eps]
    if not over:
        return LawTestReceipt(
            law="II", tier="T1", outcome=PASS,
            detail=f"all loops' prediction error ≤ ε_loop={eps}",
            predicate_id="law2_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": len(loops), "epsilon_loop": eps},
        )
    return LawTestReceipt(
        law="II", tier="T1", outcome=FAIL,
        detail=f"{len(over)} loops exceed ε_loop={eps}",
        predicate_id="law2_T1",
        timestamp_utc=harness_state.now(),
        evidence={"violators": [lp.get("id") for lp in over]},
    )


def law2_T2(harness_state, evidence_set) -> LawTestReceipt:
    """cross_loop_pred.mse ≤ ε_integrated."""
    council = harness_state.observed.get("council", None)
    if not council:
        return LawTestReceipt(
            law="II", tier="T2", outcome=NOT_APPLICABLE,
            detail="no council — cross_loop_pred N/A",
            predicate_id="law2_T2",
            timestamp_utc=harness_state.now(),
            evidence={"council_present": False},
        )
    mse = council.get("cross_loop_pred_mse", None)
    eps = harness_state.thresholds.get("T2", {}).get("epsilon_integrated", 0.15)
    if mse is None:
        return LawTestReceipt(
            law="II", tier="T2", outcome=NOT_APPLICABLE,
            detail="council present but cross_loop_pred_mse not measured",
            predicate_id="law2_T2",
            timestamp_utc=harness_state.now(),
            evidence={"mse_measured": False},
        )
    if mse <= eps:
        return LawTestReceipt(
            law="II", tier="T2", outcome=PASS,
            detail=f"cross_loop_pred.mse={mse} ≤ ε_integrated={eps}",
            predicate_id="law2_T2",
            timestamp_utc=harness_state.now(),
            evidence={"mse": mse, "eps": eps},
        )
    return LawTestReceipt(
        law="II", tier="T2", outcome=FAIL,
        detail=f"cross_loop_pred.mse={mse} > ε_integrated={eps}",
        predicate_id="law2_T2",
        timestamp_utc=harness_state.now(),
        evidence={"mse": mse, "eps": eps},
    )


def law2_T3(harness_state, evidence_set) -> LawTestReceipt:
    """future_self_model.predict(t+Δ).distance(self(t+Δ)) ≤ ε_3."""
    sm = harness_state.observed.get("self_model", None)
    if not sm:
        return LawTestReceipt(
            law="II", tier="T3", outcome=NOT_APPLICABLE,
            detail="no self_model — future_self_model.predict N/A",
            predicate_id="law2_T3",
            timestamp_utc=harness_state.now(),
            evidence={"self_model_present": False},
        )
    dist = sm.get("predicted_self_distance", None)
    eps = harness_state.thresholds.get("T3", {}).get("epsilon_3", 0.2)
    if dist is None:
        return LawTestReceipt(
            law="II", tier="T3", outcome=NOT_APPLICABLE,
            detail="self_model present but predicted_self_distance not measured",
            predicate_id="law2_T3",
            timestamp_utc=harness_state.now(),
            evidence={"measured": False},
        )
    if dist <= eps:
        return LawTestReceipt(
            law="II", tier="T3", outcome=PASS,
            detail=f"self-prediction distance={dist} ≤ ε_3={eps}",
            predicate_id="law2_T3",
            timestamp_utc=harness_state.now(),
            evidence={"distance": dist, "eps": eps},
        )
    return LawTestReceipt(
        law="II", tier="T3", outcome=FAIL,
        detail=f"self-prediction distance={dist} > ε_3={eps}",
        predicate_id="law2_T3",
        timestamp_utc=harness_state.now(),
        evidence={"distance": dist, "eps": eps},
    )


def law2_T4(harness_state, evidence_set) -> LawTestReceipt:
    """peer_predictions.calibrated() (Brier score ≤ ε_4)."""
    swarm = harness_state.observed.get("swarm", None)
    if not swarm:
        return LawTestReceipt(
            law="II", tier="T4", outcome=NOT_APPLICABLE,
            detail="no swarm — peer_predictions N/A",
            predicate_id="law2_T4",
            timestamp_utc=harness_state.now(),
            evidence={"swarm_present": False},
        )
    brier = swarm.get("peer_brier_score", None)
    eps = harness_state.thresholds.get("T4", {}).get("epsilon_4", 0.25)
    if brier is None:
        return LawTestReceipt(
            law="II", tier="T4", outcome=NOT_APPLICABLE,
            detail="swarm present but peer_brier_score not measured",
            predicate_id="law2_T4",
            timestamp_utc=harness_state.now(),
            evidence={"measured": False},
        )
    if brier <= eps:
        return LawTestReceipt(
            law="II", tier="T4", outcome=PASS,
            detail=f"peer Brier score={brier} ≤ ε_4={eps}",
            predicate_id="law2_T4",
            timestamp_utc=harness_state.now(),
            evidence={"brier": brier, "eps": eps},
        )
    return LawTestReceipt(
        law="II", tier="T4", outcome=FAIL,
        detail=f"peer Brier score={brier} > ε_4={eps}",
        predicate_id="law2_T4",
        timestamp_utc=harness_state.now(),
        evidence={"brier": brier, "eps": eps},
    )


# ============================================================
# Law III — Adaptation (parameter updates after outcomes)
# ============================================================

def law3_T0(harness_state, evidence_set) -> LawTestReceipt:
    """Trivial-true by design — T0 has nothing to adapt.

    Plate 10 footer: 'Law III at T0 is trivial-true by design; T0 has
    nothing to adapt.' This predicate always returns PASS with that
    explanation in detail.
    """
    return LawTestReceipt(
        law="III", tier="T0", outcome=PASS,
        detail="trivial-true by design (plate 10 footer: T0 has nothing to adapt)",
        predicate_id="law3_T0",
        timestamp_utc=harness_state.now(),
        evidence={"trivial_true": True},
    )


def law3_T1(harness_state, evidence_set) -> LawTestReceipt:
    """∀ loop: gradient_update_applied(loop, outcome)."""
    loops = harness_state.observed.get("loops", None)
    if not loops:
        return LawTestReceipt(
            law="III", tier="T1", outcome=NOT_APPLICABLE,
            detail="no loops observed",
            predicate_id="law3_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": 0},
        )
    not_updated = [lp for lp in loops
                   if not lp.get("gradient_update_applied", False)]
    if not not_updated:
        return LawTestReceipt(
            law="III", tier="T1", outcome=PASS,
            detail=f"all {len(loops)} loops applied gradient updates after outcomes",
            predicate_id="law3_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": len(loops)},
        )
    return LawTestReceipt(
        law="III", tier="T1", outcome=FAIL,
        detail=f"{len(not_updated)} loops did not apply gradient updates",
        predicate_id="law3_T1",
        timestamp_utc=harness_state.now(),
        evidence={"non_updating_loops": [lp.get("id") for lp in not_updated]},
    )


def law3_T2(harness_state, evidence_set) -> LawTestReceipt:
    """cross_loop_tuning_observed_in(history)."""
    council = harness_state.observed.get("council", None)
    if not council:
        return LawTestReceipt(
            law="III", tier="T2", outcome=NOT_APPLICABLE,
            detail="no council — cross_loop_tuning N/A",
            predicate_id="law3_T2",
            timestamp_utc=harness_state.now(),
            evidence={"council_present": False},
        )
    observed = council.get("cross_loop_tuning_observed", False)
    if observed:
        return LawTestReceipt(
            law="III", tier="T2", outcome=PASS,
            detail="cross_loop_tuning observed in history",
            predicate_id="law3_T2",
            timestamp_utc=harness_state.now(),
            evidence=council,
        )
    return LawTestReceipt(
        law="III", tier="T2", outcome=FAIL,
        detail="cross_loop_tuning not observed in history",
        predicate_id="law3_T2",
        timestamp_utc=harness_state.now(),
        evidence=council,
    )


def law3_T3(harness_state, evidence_set) -> LawTestReceipt:
    """plan_revised_after(failure_event)."""
    plan = harness_state.observed.get("plan", None)
    if not plan:
        return LawTestReceipt(
            law="III", tier="T3", outcome=NOT_APPLICABLE,
            detail="no plan — plan_revised_after N/A",
            predicate_id="law3_T3",
            timestamp_utc=harness_state.now(),
            evidence={"plan_present": False},
        )
    if plan.get("revised_after_failure", False):
        return LawTestReceipt(
            law="III", tier="T3", outcome=PASS,
            detail="plan revised after observed failure event",
            predicate_id="law3_T3",
            timestamp_utc=harness_state.now(),
            evidence=plan,
        )
    return LawTestReceipt(
        law="III", tier="T3", outcome=FAIL,
        detail="plan did not revise after failure event",
        predicate_id="law3_T3",
        timestamp_utc=harness_state.now(),
        evidence=plan,
    )


def law3_T4(harness_state, evidence_set) -> LawTestReceipt:
    """policy_updated_after(swarm_outcome)."""
    swarm = harness_state.observed.get("swarm", None)
    if not swarm:
        return LawTestReceipt(
            law="III", tier="T4", outcome=NOT_APPLICABLE,
            detail="no swarm — policy_updated_after N/A",
            predicate_id="law3_T4",
            timestamp_utc=harness_state.now(),
            evidence={"swarm_present": False},
        )
    if swarm.get("policy_updated_after_outcome", False):
        return LawTestReceipt(
            law="III", tier="T4", outcome=PASS,
            detail="policy updated after swarm outcome",
            predicate_id="law3_T4",
            timestamp_utc=harness_state.now(),
            evidence=swarm,
        )
    return LawTestReceipt(
        law="III", tier="T4", outcome=FAIL,
        detail="policy did not update after swarm outcome",
        predicate_id="law3_T4",
        timestamp_utc=harness_state.now(),
        evidence=swarm,
    )


# ============================================================
# Agentic Corollary — closed perceive→act loops
# ============================================================

def agentic_T0(harness_state, evidence_set) -> LawTestReceipt:
    """exists_loop(perceive, act)."""
    loop = harness_state.observed.get("perceive_act_loop", None)
    if loop is None:
        return LawTestReceipt(
            law="agentic", tier="T0", outcome=NOT_APPLICABLE,
            detail="harness did not report perceive_act_loop",
            predicate_id="agentic_T0",
            timestamp_utc=harness_state.now(),
            evidence={"missing_key": "perceive_act_loop"},
        )
    if loop.get("exists", False):
        return LawTestReceipt(
            law="agentic", tier="T0", outcome=PASS,
            detail="perceive→act loop exists",
            predicate_id="agentic_T0",
            timestamp_utc=harness_state.now(),
            evidence=loop,
        )
    return LawTestReceipt(
        law="agentic", tier="T0", outcome=FAIL,
        detail="no perceive→act loop — system does not close on observation",
        predicate_id="agentic_T0",
        timestamp_utc=harness_state.now(),
        evidence=loop,
    )


def agentic_T1(harness_state, evidence_set) -> LawTestReceipt:
    """∀ loop: exists_full_cycle(perceive, predict, act, observe)."""
    loops = harness_state.observed.get("loops", None)
    if not loops:
        return LawTestReceipt(
            law="agentic", tier="T1", outcome=NOT_APPLICABLE,
            detail="no loops observed",
            predicate_id="agentic_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": 0},
        )
    incomplete = [lp for lp in loops
                  if not lp.get("full_cycle_perceive_predict_act_observe", False)]
    if not incomplete:
        return LawTestReceipt(
            law="agentic", tier="T1", outcome=PASS,
            detail=f"all {len(loops)} loops have full perceive-predict-act-observe cycle",
            predicate_id="agentic_T1",
            timestamp_utc=harness_state.now(),
            evidence={"loops_count": len(loops)},
        )
    return LawTestReceipt(
        law="agentic", tier="T1", outcome=FAIL,
        detail=f"{len(incomplete)} loops missing full cycle",
        predicate_id="agentic_T1",
        timestamp_utc=harness_state.now(),
        evidence={"incomplete": [lp.get("id") for lp in incomplete]},
    )


def agentic_T2(harness_state, evidence_set) -> LawTestReceipt:
    """orchestrator.closes_loop_across(loops)."""
    council = harness_state.observed.get("council", None)
    if not council:
        return LawTestReceipt(
            law="agentic", tier="T2", outcome=NOT_APPLICABLE,
            detail="no council — orchestrator.closes_loop_across N/A",
            predicate_id="agentic_T2",
            timestamp_utc=harness_state.now(),
            evidence={"council_present": False},
        )
    if council.get("orchestrator_closes_loop_across_loops", False):
        return LawTestReceipt(
            law="agentic", tier="T2", outcome=PASS,
            detail="orchestrator closes loop across council loops",
            predicate_id="agentic_T2",
            timestamp_utc=harness_state.now(),
            evidence=council,
        )
    return LawTestReceipt(
        law="agentic", tier="T2", outcome=FAIL,
        detail="orchestrator does not close loop across council loops",
        predicate_id="agentic_T2",
        timestamp_utc=harness_state.now(),
        evidence=council,
    )


def agentic_T3(harness_state, evidence_set) -> LawTestReceipt:
    """temporal_loop_closure(self_at_t, self_at_t+Δ)."""
    sm = harness_state.observed.get("self_model", None)
    if not sm:
        return LawTestReceipt(
            law="agentic", tier="T3", outcome=NOT_APPLICABLE,
            detail="no self_model — temporal_loop_closure N/A",
            predicate_id="agentic_T3",
            timestamp_utc=harness_state.now(),
            evidence={"self_model_present": False},
        )
    if sm.get("temporal_loop_closure", False):
        return LawTestReceipt(
            law="agentic", tier="T3", outcome=PASS,
            detail="temporal loop closure verified self_at_t → self_at_t+Δ",
            predicate_id="agentic_T3",
            timestamp_utc=harness_state.now(),
            evidence=sm,
        )
    return LawTestReceipt(
        law="agentic", tier="T3", outcome=FAIL,
        detail="temporal loop closure not verified",
        predicate_id="agentic_T3",
        timestamp_utc=harness_state.now(),
        evidence=sm,
    )


def agentic_T4(harness_state, evidence_set) -> LawTestReceipt:
    """multi_agent_loop_closure(self, peers)."""
    swarm = harness_state.observed.get("swarm", None)
    if not swarm:
        return LawTestReceipt(
            law="agentic", tier="T4", outcome=NOT_APPLICABLE,
            detail="no swarm — multi_agent_loop_closure N/A",
            predicate_id="agentic_T4",
            timestamp_utc=harness_state.now(),
            evidence={"swarm_present": False},
        )
    if swarm.get("multi_agent_loop_closure", False):
        return LawTestReceipt(
            law="agentic", tier="T4", outcome=PASS,
            detail="multi-agent loop closure verified across swarm",
            predicate_id="agentic_T4",
            timestamp_utc=harness_state.now(),
            evidence=swarm,
        )
    return LawTestReceipt(
        law="agentic", tier="T4", outcome=FAIL,
        detail="multi-agent loop closure not verified",
        predicate_id="agentic_T4",
        timestamp_utc=harness_state.now(),
        evidence=swarm,
    )


# ============================================================
# Registry — ordered list per plate 10 causal order (0→I→II→III→agentic)
# ============================================================

PREDICATE_REGISTRY: List[Tuple[str, Callable]] = [
    ("law0_T0", law0_T0),
    ("law0_T1", law0_T1),
    ("law0_T2", law0_T2),
    ("law0_T3", law0_T3),
    ("law0_T4", law0_T4),
    ("law1_T0", law1_T0),
    ("law1_T1", law1_T1),
    ("law1_T2", law1_T2),
    ("law1_T3", law1_T3),
    ("law1_T4", law1_T4),
    ("law2_T0", law2_T0),
    ("law2_T1", law2_T1),
    ("law2_T2", law2_T2),
    ("law2_T3", law2_T3),
    ("law2_T4", law2_T4),
    ("law3_T0", law3_T0),
    ("law3_T1", law3_T1),
    ("law3_T2", law3_T2),
    ("law3_T3", law3_T3),
    ("law3_T4", law3_T4),
    ("agentic_T0", agentic_T0),
    ("agentic_T1", agentic_T1),
    ("agentic_T2", agentic_T2),
    ("agentic_T3", agentic_T3),
    ("agentic_T4", agentic_T4),
]

# Sanity check: registry must contain 25 entries (5 laws × 5 tiers)
assert len(PREDICATE_REGISTRY) == 25, (
    f"PREDICATE_REGISTRY must contain 25 predicates "
    f"(5 laws × 5 tiers), got {len(PREDICATE_REGISTRY)}"
)
