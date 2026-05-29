"""Anvil-Pantheon-Floor — Admission Gate framework (Packet 11).

The Admission Gate is the apparatus that proves the floor pipeline
meets gold expectations for a labeled fixture set. It runs the full
end-to-end pipeline (Scout output -> guarded book -> 3 substrates ->
Hermes bundle -> Oracle -> certificate) against each fixture and
compares the result to the gold behavior.

Gold-comparison logic:
  - SCORE expectation: pipeline must NOT refuse AND the emitted
    lead_band must match the gold lead_band.
  - REFUSE_TO_CERTIFY expectation: pipeline MUST refuse AND
    refusal_reasons must be non-empty (a refusal without a reason
    chain is opaque and not auditable).

NON_CLAIMS:
  - Does NOT compute substrate outputs
  - Does NOT modify pipeline behavior
  - Does NOT define the gold; the gold is the input
  - Only EVALUATES: runs the pipeline, compares to gold, reports

Floor scope (deliberately limited):
  - Takes a list of (FixtureSpec, scout_output_dict) tuples as input
  - Runs the full pipeline for each
  - Aggregates per-fixture verdicts into an AdmissionGateResult
  - Honest about source: each FixtureSpec carries `source` =
    "synthetic" or "scout_derived" so consumers know what's been
    tested against real Scout output vs manufactured representations.

What's NOT in this packet:
  - Routing real-time Scout output (P13 bridge)
  - Threshold calibration based on gate failures (P12)
  - Bulk regression sweeps over thousands of cases (not floor scope)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .cognitive import CANONICAL_REGISTRY
from .ingress_guard import guard_ingress
from .integration.scout_adapter import adapt_scout_output
from .oracle import OracleResult, compose
from .services.hermes import bundle_substrates
from .substrates.hestia import compute_hestia
from .substrates.indra import compute_indra
from .substrates.vesta import compute_vesta


# ─── Constants ────────────────────────────────────────────────────────────

# Closed set of gold behavior expectations
GOLD_BEHAVIOR_SCORE = "SCORE"
GOLD_BEHAVIOR_REFUSE = "REFUSE_TO_CERTIFY"
KNOWN_GOLD_BEHAVIORS: Tuple[str, ...] = (GOLD_BEHAVIOR_SCORE, GOLD_BEHAVIOR_REFUSE)

# Closed set of fixture sources
SOURCE_SYNTHETIC = "synthetic"
SOURCE_SCOUT_DERIVED = "scout_derived"
KNOWN_SOURCES: Tuple[str, ...] = (SOURCE_SYNTHETIC, SOURCE_SCOUT_DERIVED)

# Closed set of verdict statuses
VERDICT_PASSED = "passed"
VERDICT_FAILED = "failed"


# ─── Fixture spec & verdict types ─────────────────────────────────────────

@dataclass(frozen=True)
class FixtureSpec:
    """The gold expectation for one fixture.
      - case_id: unique fixture identifier (e.g. "ACF_E01")
      - gold_behavior: SCORE or REFUSE_TO_CERTIFY
      - gold_lead_band: low / medium / high (None if REFUSE_TO_CERTIFY)
      - source: synthetic (manufactured for floor) or scout_derived
        (real Scout output captured)
      - notes: free-text rationale for the spec
    """
    case_id: str
    gold_behavior: str
    gold_lead_band: Optional[str]
    source: str
    notes: str = ""

    def __post_init__(self):
        if self.gold_behavior not in KNOWN_GOLD_BEHAVIORS:
            raise ValueError(
                f"gold_behavior must be one of {KNOWN_GOLD_BEHAVIORS}; "
                f"got {self.gold_behavior!r}"
            )
        if self.source not in KNOWN_SOURCES:
            raise ValueError(
                f"source must be one of {KNOWN_SOURCES}; got {self.source!r}"
            )
        if self.gold_behavior == GOLD_BEHAVIOR_SCORE and self.gold_lead_band is None:
            raise ValueError(
                f"SCORE fixtures must declare gold_lead_band; "
                f"{self.case_id} has gold_lead_band=None"
            )


@dataclass(frozen=True)
class FixtureVerdict:
    """Per-fixture outcome of running the pipeline against a fixture.
      - case_id: matches the spec
      - status: passed or failed
      - reason: human-readable explanation
      - pipeline_refused: True if the pipeline refused
      - emitted_lead_band: what the pipeline emitted (or None if refused)
      - refusal_reasons: refusal chain (empty if emitted)
      - certificate_id: traceability to the certificate
    """
    case_id: str
    status: str
    reason: str
    pipeline_refused: bool
    emitted_lead_band: Optional[str]
    refusal_reasons: Tuple[str, ...]
    certificate_id: str

    def passed(self) -> bool:
        return self.status == VERDICT_PASSED


@dataclass(frozen=True)
class AdmissionGateResult:
    """The full admission-gate run summary.
      - overall_passed: True iff ALL fixtures passed
      - verdicts: per-fixture verdicts in the order given
      - summary: counts by status
    """
    overall_passed: bool
    verdicts: Tuple[FixtureVerdict, ...]
    summary: Dict[str, int] = field(default_factory=dict)

    def by_status(self, status: str) -> Tuple[FixtureVerdict, ...]:
        return tuple(v for v in self.verdicts if v.status == status)

    def failures(self) -> Tuple[FixtureVerdict, ...]:
        return self.by_status(VERDICT_FAILED)


# ─── Pipeline runner (encapsulates the full Scout -> certificate flow) ───

def _run_pipeline(scout_output: Dict[str, Any], case_id: str) -> OracleResult:
    """Run the full end-to-end pipeline for one Scout output.
    Deterministic: emission_id derived from case_id, timestamp fixed."""
    book = adapt_scout_output(scout_output)
    safe = guard_ingress(book).safe_book
    h = compute_hestia(safe)
    v = compute_vesta(safe)
    i = compute_indra(safe)
    bundle = bundle_substrates(h, v, i, safe.content_hash())
    return compose(
        sourcebook=safe,
        bundle=bundle,
        registry=CANONICAL_REGISTRY,
        scout_output_hash=safe.content_hash(),
        emission_id=f"01HXY_{case_id}",
        timestamp="2026-05-29T00:00:00Z",
        now_secs=0.0,
    )


# ─── Single-fixture evaluation ────────────────────────────────────────────

def evaluate_single_fixture(
    spec: FixtureSpec,
    scout_output: Dict[str, Any],
) -> FixtureVerdict:
    """Run the pipeline against one fixture and compare to gold."""
    result = _run_pipeline(scout_output, spec.case_id)

    # Extract what the pipeline actually did
    pipeline_refused = result.refused
    emitted_band = None
    if not pipeline_refused:
        hestia_out = result.certificate.substrate_outputs.get("hestia")
        if hestia_out is not None:
            emitted_band = hestia_out.output_payload.get("lead_band")

    base_fields = {
        "case_id": spec.case_id,
        "pipeline_refused": pipeline_refused,
        "emitted_lead_band": emitted_band,
        "refusal_reasons": result.refusal_reasons,
        "certificate_id": result.certificate.certificate_id,
    }

    # ─── Gold comparison ──────────────────────────────────────────────
    if spec.gold_behavior == GOLD_BEHAVIOR_SCORE:
        # Must NOT have refused
        if pipeline_refused:
            return FixtureVerdict(
                status=VERDICT_FAILED,
                reason=(
                    f"gold=SCORE but pipeline refused; "
                    f"refusal_reasons={list(result.refusal_reasons)}"
                ),
                **base_fields,
            )
        # Band must match
        if emitted_band != spec.gold_lead_band:
            return FixtureVerdict(
                status=VERDICT_FAILED,
                reason=(
                    f"gold_lead_band={spec.gold_lead_band!r} but pipeline "
                    f"emitted {emitted_band!r}"
                ),
                **base_fields,
            )
        return FixtureVerdict(
            status=VERDICT_PASSED,
            reason=f"emitted {emitted_band!r} band as expected",
            **base_fields,
        )

    # gold_behavior == REFUSE_TO_CERTIFY
    if not pipeline_refused:
        return FixtureVerdict(
            status=VERDICT_FAILED,
            reason=(
                f"gold=REFUSE_TO_CERTIFY but pipeline emitted "
                f"lead_band={emitted_band!r}"
            ),
            **base_fields,
        )
    if not result.refusal_reasons:
        # JB-P11-3 discipline: refusal without a reason chain is opaque
        return FixtureVerdict(
            status=VERDICT_FAILED,
            reason="pipeline refused but refusal_reasons is empty (no auditable reason)",
            **base_fields,
        )
    return FixtureVerdict(
        status=VERDICT_PASSED,
        reason=f"refused with reasons: {list(result.refusal_reasons)}",
        **base_fields,
    )


# ─── Multi-fixture gate runner ────────────────────────────────────────────

def run_admission_gate(
    fixtures: List[Tuple[FixtureSpec, Dict[str, Any]]],
) -> AdmissionGateResult:
    """Run the admission gate over a list of (spec, scout_output) tuples.
    Returns AdmissionGateResult with overall + per-fixture verdicts.

    JB-P11-5: gate accepts ANY fixture list; not coupled to a
    specific fixture set."""
    verdicts: List[FixtureVerdict] = []
    for spec, scout_output in fixtures:
        verdicts.append(evaluate_single_fixture(spec, scout_output))

    summary = {
        VERDICT_PASSED: sum(1 for v in verdicts if v.passed()),
        VERDICT_FAILED: sum(1 for v in verdicts if not v.passed()),
    }
    overall_passed = summary[VERDICT_FAILED] == 0 and len(verdicts) > 0

    return AdmissionGateResult(
        overall_passed=overall_passed,
        verdicts=tuple(verdicts),
        summary=summary,
    )
