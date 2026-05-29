"""Anvil-Pantheon-Floor — Hestia substrate organ (Packet 5).

Hestia owns the MACHINE/DETERMINISTIC math soil. This is the first
substrate organ to define a full math-soil passport per the v0.37
Pantheon doctrine.

PASSPORT (v0.37 format):

  Math soil:    machine / deterministic with bounded state
  Native object: Scout's five channel scores + V/W/M counts + the
                rubric-derived lead_band and gate states. All fields
                are exactly computable from inputs; no probability,
                no semantic inference, no learned weights.
  Update law:   deterministic re-computation when input changes.
                Floor: STATELESS (no internal mutating parameters).
                Bounded-update law is trivially satisfied. Real
                bounded learning lands in Packet 12.
  Beats baseline where:
                Anywhere correctness is exactly computable from Scout
                outputs -- lead-band classification under explicit
                cuts, named gate states (thin_scrape, signal_density,
                decision_maker, non_commercial), V/W/M arithmetic
                invariants. The substrate federation needs a uniform,
                hashable, citable rendering of Scout's deterministic
                state.
  Killing ablation:
                Strip Scout's channel scores and V/W/M evidence from
                the SourceBook. Hestia degenerates to a no_signal state
                (all zeros, all gates raised). This confirms its math
                soil is consuming the deterministic input, not faking.
  Forbidden impersonations:
                * Vesta (probability-native uncertainty) -- Hestia
                  must NOT compute posteriors, credibilities, or any
                  probability distribution.
                * Indra (wave/phase coherence) -- Hestia must NOT
                  compute cross-channel agreement, interference, or
                  coherence scores.
                * Oracle (composition + emission) -- Hestia must NOT
                  emit prose, select templates, or fill slots.

NON_CLAIMS (the impersonation-forbidding discipline made callable):
  - No method named .posterior(), .credibility(), .belief()
  - No method named .coherence(), .interference(), .agreement()
  - No method named .compose(), .emit(), .render(), .write()
  - No floating-point probabilities in output_payload (only the
    signal_density echo from Scout, which is a STRUCTURAL ratio per
    Scout's own contract, NOT a probability -- the rubric carries
    this distinction)

Floor scope:
  - Stateless: each compute_hestia(book) call is a pure function
  - Input: SourceBook (post-ingress-guard, with scout_metadata)
  - Output: SubstrateOutput with substrate_kind=HESTIA
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

from ..sourcebook import SourceBook
from ..types import (
    EvidenceKind,
    SubstrateKind,
    SubstrateOutput,
    _sha256_of,
)


# ─── Passport (machine-readable v0.37 doctrine entry) ─────────────────────

HESTIA_PASSPORT: Dict[str, Any] = {
    "name":          "hestia",
    "math_soil":     "machine.deterministic_bounded_state",
    "native_object": "channel_scores + lead_band + gate_states + vwm_counts",
    "update_law":    "stateless_re_computation",   # Floor; bounded-learning in P12
    "beats_baseline_where": (
        "lead_band_classification",
        "explicit_named_gates",
        "vwm_arithmetic_invariants",
        "deterministic_state_hashing",
    ),
    "killing_ablation": "strip_channel_scores_and_vwm_evidence",
    "forbidden_impersonations": (
        "vesta.posterior",
        "vesta.credibility",
        "indra.coherence",
        "indra.interference",
        "oracle.compose",
        "oracle.emit",
    ),
    "non_claims_method_names_must_not_exist": (
        "posterior", "credibility", "belief",
        "coherence", "interference", "agreement",
        "compose", "emit", "render", "write",
    ),
}


# ─── Constants for derived classifications ────────────────────────────────

# ACF-0 lead-band cuts (per CheeseForge spec): low [0,33], medium [34,66],
# high [67,100]. These are deterministic ranges; Vesta's posterior over
# the band lives elsewhere and uses these cuts as its observation model.
LEAD_BAND_CUTS: Tuple[Tuple[str, int, int], ...] = (
    ("low",    0,  33),
    ("medium", 34, 66),
    ("high",   67, 100),
)

# Gate thresholds (floor; mutable via Packet 12 bounded learning later).
SIGNAL_DENSITY_FLOOR = 0.2           # below this, signal_density_gate fires
DECISION_MAKER_SENIORITY_FLOOR = 10  # below this AND not decision_maker -> non_dm_gate


# ─── Hestia's native object ───────────────────────────────────────────────

@dataclass(frozen=True)
class HestiaNativeObject:
    """Hestia's deterministic state derived from a SourceBook. Every
    field is exactly computable; no probability, no semantic content.
    Frozen so it's safely hashable; serialized into SubstrateOutput's
    output_payload for the receipt."""

    # ─── Echo from Scout (verbatim, no derivation) ───
    channel_scores: Dict[str, int]   # industry_fit, company_size_fit, etc.
    lead_score: int                  # echo from scout_metadata
    signal_density: float            # STRUCTURAL ratio, NOT a probability
    thin_scrape: bool

    # ─── Computed from SourceBook ───
    verified_count: int
    weak_count: int
    missing_count: int

    # ─── Derived (deterministic from above) ───
    lead_band: str                   # one of "low" | "medium" | "high"
    gate_states: Dict[str, bool]     # explicit named gates

    def to_payload(self) -> Dict[str, Any]:
        """JSON-safe dict for inclusion in SubstrateOutput.output_payload."""
        return {
            "channel_scores": dict(sorted(self.channel_scores.items())),
            "lead_score": self.lead_score,
            "signal_density": self.signal_density,
            "thin_scrape": self.thin_scrape,
            "verified_count": self.verified_count,
            "weak_count": self.weak_count,
            "missing_count": self.missing_count,
            "lead_band": self.lead_band,
            "gate_states": dict(sorted(self.gate_states.items())),
        }


# ─── Helpers ──────────────────────────────────────────────────────────────

def _lead_band_from_score(score: int) -> str:
    """Map lead_score to band via ACF-0 cuts. Defensive: out-of-range
    inputs clamp to the nearest band."""
    if score < 0:
        return "low"
    for name, lo, hi in LEAD_BAND_CUTS:
        if lo <= score <= hi:
            return name
    return "high"  # >100 (defensive)


def _compute_gate_states(
    *,
    signal_density: float,
    thin_scrape: bool,
    decision_maker: bool,
    decision_maker_seniority: int,
    industry_fit: int,
    company_size_fit: int,
) -> Dict[str, bool]:
    """Compute named gate states. Each gate True = condition fires
    (i.e., the gate is RAISED, indicating a concern). The semantics
    are deliberately uniform: True means 'flag set'."""
    return {
        # Scout itself reported thin scrape -- structural refusal cue
        "thin_scrape_gate": bool(thin_scrape),
        # Signal density below floor -- insufficient evidence
        "signal_density_gate": signal_density < SIGNAL_DENSITY_FLOOR,
        # Not a decision-maker AND low seniority -- likely wrong target
        "non_decision_maker_gate": (
            (not decision_maker) and decision_maker_seniority < DECISION_MAKER_SENIORITY_FLOOR
        ),
        # No industry signal AND no company-size signal -- likely
        # non-commercial entity (nonprofit / consumer / etc.)
        "non_commercial_gate": (industry_fit == 0 and company_size_fit == 0),
    }


# ─── Public entry point ───────────────────────────────────────────────────

def compute_hestia(book: SourceBook) -> SubstrateOutput:
    """Compute Hestia's output for a given SourceBook. Pure deterministic
    function: same book -> identical SubstrateOutput. The output's
    native_object_hash hashes the JSON-canonical payload; the
    output_payload is the dict-serialized HestiaNativeObject.

    NON_CLAIMS: this function does not compute probability, does not
    compute semantic coherence, does not emit prose. It surfaces
    Scout's deterministic state in a hashable receipt-ready form plus
    the derived lead_band and gate_states.
    """
    meta = book.scout_metadata or {}

    # Channel scores -- echo from Scout
    channel_scores = {
        ch: int(meta.get(ch, 0))
        for ch in (
            "industry_fit",
            "company_size_fit",
            "decision_maker_seniority",
            "budget_likelihood_score",
            "growth_signals",
        )
    }
    lead_score = int(meta.get("lead_score", 0))

    sig_ev = meta.get("signal_evidence", {}) or {}
    signal_density = float(sig_ev.get("signal_density", 0.0))
    thin_scrape = bool(sig_ev.get("thin_scrape", False))

    decision_maker = bool(meta.get("decision_maker", False))

    # V/W/M counts from the SourceBook itself
    verified_count = len(book.by_evidence_kind(EvidenceKind.VERIFIED))
    weak_count = len(book.by_evidence_kind(EvidenceKind.WEAK))
    missing_count = len(book.by_evidence_kind(EvidenceKind.MISSING))

    # Derived classifications
    lead_band = _lead_band_from_score(lead_score)
    gate_states = _compute_gate_states(
        signal_density=signal_density,
        thin_scrape=thin_scrape,
        decision_maker=decision_maker,
        decision_maker_seniority=channel_scores["decision_maker_seniority"],
        industry_fit=channel_scores["industry_fit"],
        company_size_fit=channel_scores["company_size_fit"],
    )

    native = HestiaNativeObject(
        channel_scores=channel_scores,
        lead_score=lead_score,
        signal_density=signal_density,
        thin_scrape=thin_scrape,
        verified_count=verified_count,
        weak_count=weak_count,
        missing_count=missing_count,
        lead_band=lead_band,
        gate_states=gate_states,
    )

    payload = native.to_payload()
    return SubstrateOutput(
        substrate_kind=SubstrateKind.HESTIA,
        native_object_hash=_sha256_of(payload),
        output_payload=payload,
    )
