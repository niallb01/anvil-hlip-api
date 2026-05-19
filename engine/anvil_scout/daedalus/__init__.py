"""Daedalus law-conformance harness for Anvil-Scout.

This namespace holds the Daedalus AGI-law predicate matrix (plate 10) compiled
to Python, the receipt data shape that every predicate emits, and the harness
that runs predicates against a target system.

TB-11 ships these files in observation-only mode — they evaluate v0.1.0's
current state and produce a baseline T0 tier receipt. No production behavior
changes in TB-11.

Predicate convention (from plate 10 footer):
    - All predicates return bool. False = test fails = gate blocked.
    - All predicates take (harness_state, evidence_set) as arguments.
    - All predicates emit a LawTestReceipt regardless of pass/fail.
    - ε bounds are tier-dependent thresholds defined in threshold_table.
    - Law order is causal: 0 → I → II → III → Agentic.
      A failure at law N invalidates evaluation of laws N+1.
    - Law III at T0 is trivial-true by design; T0 has nothing to adapt.

This module is internal observation infrastructure — it must not leak into
partner-facing output per v2 non-negotiable #5 (no Daedalus leakage).
"""

from anvil_scout.daedalus.receipts import LawTestReceipt
from anvil_scout.daedalus.predicates import (
    PREDICATE_REGISTRY,
    threshold_table,
)
from anvil_scout.daedalus.harness import (
    HarnessState,
    EvidenceSet,
    run_all_predicates,
    summarize_receipts,
    observe_v01,
    observe_v2_TB14,
)
# TB-12 surface
from anvil_scout.daedalus.modes import ExecutionMode, parse_mode
from anvil_scout.daedalus.state import (
    StateProvider,
    NullStateProvider,
    SQLiteStateProvider,
    initial_state,
    migrate_state,
    register_migration,
    CURRENT_SCHEMA_VERSION,
    assert_no_pii_in_state,
)
from anvil_scout.daedalus.outcomes import (
    Outcome,
    OutcomeLabel,
    OutcomeProvider,
    NullOutcomeProvider,
    make_outcome,
)
# TB-14 surface
from anvil_scout.daedalus.adapters import (
    DetectorAdapter,
    default_adapter,
    default_adapters,
    update_adapter,
    update_all_adapters,
    apply_adapters_to_spans,
    adapters_from_state,
    adapters_to_state,
    MIN_SPAN_LENGTH_CEIL,
)
# TB-13 surface
from anvil_scout.daedalus.observability import (
    DETECTOR_NAMES,
    DetectorReceipt,
    hash_text,
    observe_detectors,
    aggregate_into_state,
)
from anvil_scout.daedalus.coherence import (
    CoherenceFlag,
    FLAG_HIGH_STRIP_RATE,
    FLAG_ZERO_EVIDENCE,
    FLAG_EVIDENCE_AMP,
    SEV_INFO, SEV_WARNING, SEV_ERROR,
    detect_high_strip_rate,
    detect_zero_evidence,
    detect_evidence_amplification,
    detect_all_violations,
)

# TB-P2 predictive agent surface
from anvil_scout.daedalus.predictive import (
    PredictionReceipt,
    opaque_lead_id,
    input_fingerprint,
    ensure_prediction_state,
    features_from_payload,
    predict_from_state,
    remember_prediction_for_input,
    record_outcome,
    apply_adaptive_output,
    summarize_self_model,
)

__all__ = [
    # TB-11 predicate harness
    "LawTestReceipt",
    "PREDICATE_REGISTRY",
    "threshold_table",
    "HarnessState",
    "EvidenceSet",
    "run_all_predicates",
    "summarize_receipts",
    "observe_v01",
    "observe_v2_TB14",
    # TB-12 state + outcomes + modes
    "ExecutionMode",
    "parse_mode",
    "StateProvider",
    "NullStateProvider",
    "SQLiteStateProvider",
    "initial_state",
    "migrate_state",
    "register_migration",
    "CURRENT_SCHEMA_VERSION",
    "assert_no_pii_in_state",
    "Outcome",
    "OutcomeLabel",
    "OutcomeProvider",
    "NullOutcomeProvider",
    "make_outcome",
    # TB-13 observability + coherence
    "DETECTOR_NAMES",
    "DetectorReceipt",
    "hash_text",
    "observe_detectors",
    "aggregate_into_state",
    "CoherenceFlag",
    "FLAG_HIGH_STRIP_RATE",
    "FLAG_ZERO_EVIDENCE",
    "FLAG_EVIDENCE_AMP",
    "SEV_INFO", "SEV_WARNING", "SEV_ERROR",
    "detect_high_strip_rate",
    "detect_zero_evidence",
    "detect_evidence_amplification",
    "detect_all_violations",
    # TB-14 detector adapters
    "DetectorAdapter",
    "default_adapter",
    "default_adapters",
    "update_adapter",
    "update_all_adapters",
    "apply_adapters_to_spans",
    "adapters_from_state",
    "adapters_to_state",
    "MIN_SPAN_LENGTH_CEIL",
    # TB-P2 predictive agent
    "PredictionReceipt",
    "opaque_lead_id",
    "input_fingerprint",
    "ensure_prediction_state",
    "features_from_payload",
    "predict_from_state",
    "remember_prediction_for_input",
    "record_outcome",
    "apply_adaptive_output",
    "summarize_self_model",
]
