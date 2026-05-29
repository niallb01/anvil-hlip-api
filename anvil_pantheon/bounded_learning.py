"""Anvil-Pantheon-Floor — Bounded Learning (Packet 12).

The bounded-learning layer is how the floor improves its CALIBRATION
without changing its decision DNA. Given a history of certificates
(via Mnemosyne) and a feedback signal (LeadOutcomes), the module
proposes adjustments to a small CLOSED SET of calibratable thresholds,
each adjustment bounded by hard constraints.

The core discipline: PROPOSALS ARE RECORDS, NOT MUTATIONS. The
learning layer never changes the running system. It produces
LearningProposal records that humans or downstream process approve
(or reject) via a separate mechanism. This keeps the floor's behavior
deterministic and replay-verifiable.

NON_CLAIMS:
  - Does NOT auto-apply proposals (no apply() function in floor)
  - Does NOT modify past certificates (read-only over Mnemosyne)
  - Does NOT compute substrate outputs
  - Does NOT change Hestia's deterministic constraints (band cuts,
    signal_density floor, DM seniority floor -- these are EXCLUDED
    from the CalibratableThreshold enum)
  - Does NOT propose a new threshold below its declared minimum or
    above its declared maximum (bounded by ProposalBound)
  - Does NOT propose a single-step change larger than max_step_per_cycle
    (bounded learning means small steps, replayable history)

Floor scope:
  - 5 calibratable thresholds declared (1 with a concrete generator,
    4 reserved for future generators)
  - 1 proposal generator: propose_indra_signal_floor
  - All other generators are extension points for future packets
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .types import CertificationStatus, EmissionCertificate


# ─── Calibratable threshold registry (the CLOSED SET) ─────────────────────

class CalibratableThreshold(str, Enum):
    """The closed set of thresholds the bounded-learning layer is
    allowed to propose changes for. Hestia's deterministic constraints
    (band cuts, signal_density floor, DM seniority floor) are
    DELIBERATELY ABSENT -- they're decision DNA, not calibration."""
    INDRA_SIGNAL_FLOOR = "indra_signal_floor"
    INDRA_COHERENCE_FLOOR = "indra_coherence_floor"
    VESTA_VERIFIED_HIGH_LIKELIHOOD = "vesta_verified_high_likelihood"
    VESTA_MISSING_LOW_LIKELIHOOD = "vesta_missing_low_likelihood"
    MNEMOSYNE_DRIFT_THRESHOLD = "mnemosyne_drift_threshold"


@dataclass(frozen=True)
class ProposalBound:
    """Constraints on what values a threshold can take, and how much
    it can move in a single proposal cycle."""
    threshold: CalibratableThreshold
    minimum: float
    maximum: float
    max_step_per_cycle: float
    current_value: float

    def __post_init__(self):
        if self.minimum > self.maximum:
            raise ValueError(
                f"minimum ({self.minimum}) > maximum ({self.maximum}) "
                f"for {self.threshold.value}"
            )
        if self.max_step_per_cycle <= 0:
            raise ValueError(
                f"max_step_per_cycle must be positive; got "
                f"{self.max_step_per_cycle} for {self.threshold.value}"
            )
        if not (self.minimum <= self.current_value <= self.maximum):
            raise ValueError(
                f"current_value {self.current_value} outside bounds "
                f"[{self.minimum}, {self.maximum}] for {self.threshold.value}"
            )


# Default bounds for each threshold. These ARE bounded; they
# themselves are not calibratable (bounds are part of the floor's DNA).
DEFAULT_BOUNDS: Dict[CalibratableThreshold, ProposalBound] = {
    CalibratableThreshold.INDRA_SIGNAL_FLOOR: ProposalBound(
        threshold=CalibratableThreshold.INDRA_SIGNAL_FLOOR,
        minimum=5.0, maximum=50.0, max_step_per_cycle=5.0,
        current_value=10.0,
    ),
    CalibratableThreshold.INDRA_COHERENCE_FLOOR: ProposalBound(
        threshold=CalibratableThreshold.INDRA_COHERENCE_FLOOR,
        # coherence is a Kuramoto order parameter in [0, 1]. Max is held
        # below the calibrated emit band (>= ~0.81) so no bounded-learning
        # proposal can ever raise the floor into the region that would
        # over-refuse healthy, coherent leads. Reserved generator (no
        # propose_* yet) -- same pattern as the Vesta/Mnemosyne entries.
        minimum=0.0, maximum=0.8, max_step_per_cycle=0.05,
        current_value=0.5,
    ),
    CalibratableThreshold.VESTA_VERIFIED_HIGH_LIKELIHOOD: ProposalBound(
        threshold=CalibratableThreshold.VESTA_VERIFIED_HIGH_LIKELIHOOD,
        minimum=0.4, maximum=0.8, max_step_per_cycle=0.05,
        current_value=0.6,
    ),
    CalibratableThreshold.VESTA_MISSING_LOW_LIKELIHOOD: ProposalBound(
        threshold=CalibratableThreshold.VESTA_MISSING_LOW_LIKELIHOOD,
        minimum=0.4, maximum=0.8, max_step_per_cycle=0.05,
        current_value=0.6,
    ),
    CalibratableThreshold.MNEMOSYNE_DRIFT_THRESHOLD: ProposalBound(
        threshold=CalibratableThreshold.MNEMOSYNE_DRIFT_THRESHOLD,
        minimum=0.10, maximum=0.60, max_step_per_cycle=0.10,
        current_value=0.30,
    ),
}


# ─── Feedback signal ──────────────────────────────────────────────────────

# The closed set of outcome values for a LeadOutcome.
KNOWN_OUTCOMES: Tuple[str, ...] = ("positive", "negative", "unknown")


@dataclass(frozen=True)
class LeadOutcome:
    """A feedback record for one emitted certificate. Outcome reflects
    real-world result of acting on the lead:
      - 'positive': lead converted / was right call
      - 'negative': lead was a dud / wrong call
      - 'unknown': not yet observed / inconclusive"""
    certificate_id: str
    outcome: str
    observed_at: str
    notes: str = ""

    def __post_init__(self):
        if self.outcome not in KNOWN_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {KNOWN_OUTCOMES}; got {self.outcome!r}"
            )
        if not self.certificate_id:
            raise ValueError("certificate_id must be non-empty")


# ─── Proposal record ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class LearningProposal:
    """A proposed adjustment to one calibratable threshold. PROPOSAL
    ONLY; never auto-applied. Carries full justification chain back to
    specific certificates that drove the proposal (JB-P12-3)."""
    threshold: CalibratableThreshold
    current_value: float
    proposed_value: float
    justification_reason: str
    justification_certificate_ids: Tuple[str, ...]
    bound_applied: bool
    bound_applied_reason: str = ""

    def delta(self) -> float:
        return self.proposed_value - self.current_value


# ─── Proposal generator: Indra signal floor ───────────────────────────────

MIN_OUTCOMES_FOR_PROPOSAL = 10
"""Below this many outcomes for relevant certificates, generator returns
None (insufficient data; JB-P12-7)."""

NEAR_FLOOR_BAND_MULTIPLIER = 1.5
"""'Near the floor' means within this multiplier of the current floor.
For floor=10, near-floor is signal_magnitude in [10, 15)."""

NEGATIVE_RATE_TRIGGER = 0.50
"""If >= this fraction of near-floor certificates resulted in negative
outcomes, propose raising the floor."""


def _extract_indra_signal_magnitude(cert: EmissionCertificate) -> Optional[float]:
    """Read indra_signal_magnitude from the certificate's substrate outputs."""
    indra = cert.substrate_outputs.get("indra")
    if indra is None:
        return None
    sm = indra.output_payload.get("signal_magnitude")
    if sm is None:
        return None
    return float(sm)


def _is_emitted(cert: EmissionCertificate) -> bool:
    """True iff the certificate represents an emission (not a refusal).
    Detected by: at least one slot_fill not REFUSED."""
    return any(
        f.certification != CertificationStatus.REFUSED for f in cert.slot_fills
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def propose_indra_signal_floor(
    certificates: List[EmissionCertificate],
    outcomes: List[LeadOutcome],
    bound: Optional[ProposalBound] = None,
) -> Optional[LearningProposal]:
    """Examine emitted certificates whose Indra signal_magnitude was
    near the current floor. If a high fraction of those had negative
    outcomes, propose raising the floor (bounded by max_step_per_cycle
    and the absolute max). Returns None if insufficient data.

    Floor logic (deliberately simple):
      1. Filter certificates to those that were EMITTED (not refused)
         AND have signal_magnitude in [floor, floor * NEAR_FLOOR_BAND_MULTIPLIER)
      2. Join with outcomes on certificate_id; drop 'unknown' outcomes
      3. If joined sample < MIN_OUTCOMES_FOR_PROPOSAL, return None
      4. Compute negative_rate = neg / total
      5. If negative_rate >= NEGATIVE_RATE_TRIGGER, propose new_floor =
         min(current + max_step_per_cycle, maximum)
      6. Otherwise return None (no change proposed)
    """
    if bound is None:
        bound = DEFAULT_BOUNDS[CalibratableThreshold.INDRA_SIGNAL_FLOOR]

    current_floor = bound.current_value
    near_floor_max = current_floor * NEAR_FLOOR_BAND_MULTIPLIER

    # Index outcomes by certificate_id
    outcome_by_id: Dict[str, LeadOutcome] = {o.certificate_id: o for o in outcomes}

    # Collect near-floor emitted certificates with known outcomes
    relevant_cert_ids: List[str] = []
    negative_cert_ids: List[str] = []
    for cert in certificates:
        if not _is_emitted(cert):
            continue
        sm = _extract_indra_signal_magnitude(cert)
        if sm is None:
            continue
        if not (current_floor <= sm < near_floor_max):
            continue
        outcome = outcome_by_id.get(cert.certificate_id)
        if outcome is None or outcome.outcome == "unknown":
            continue
        relevant_cert_ids.append(cert.certificate_id)
        if outcome.outcome == "negative":
            negative_cert_ids.append(cert.certificate_id)

    if len(relevant_cert_ids) < MIN_OUTCOMES_FOR_PROPOSAL:
        return None

    negative_rate = len(negative_cert_ids) / len(relevant_cert_ids)
    if negative_rate < NEGATIVE_RATE_TRIGGER:
        return None

    # Propose a bounded step up
    raw_proposed = current_floor + bound.max_step_per_cycle
    proposed = _clamp(raw_proposed, bound.minimum, bound.maximum)
    bound_applied = (raw_proposed != proposed)
    bound_reason = ""
    if bound_applied:
        if raw_proposed > bound.maximum:
            bound_reason = f"clamped to maximum {bound.maximum}"
        elif raw_proposed < bound.minimum:
            bound_reason = f"clamped to minimum {bound.minimum}"

    return LearningProposal(
        threshold=CalibratableThreshold.INDRA_SIGNAL_FLOOR,
        current_value=current_floor,
        proposed_value=proposed,
        justification_reason=(
            f"near-floor emissions (magnitude in [{current_floor}, "
            f"{near_floor_max})) had negative_rate={negative_rate:.3f} "
            f"across {len(relevant_cert_ids)} outcomes >= trigger "
            f"{NEGATIVE_RATE_TRIGGER}"
        ),
        justification_certificate_ids=tuple(negative_cert_ids),
        bound_applied=bound_applied,
        bound_applied_reason=bound_reason,
    )
