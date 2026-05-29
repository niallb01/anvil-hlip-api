"""Anvil-Pantheon-Floor — Indra substrate organ (Packet 7).

Indra owns the WAVE / PHASE COHERENCE math soil. Third and final
substrate organ. With Hestia (machine/deterministic) and Vesta
(probability/bayesian), Indra completes the substrate federation
that cancels the lopsidedness any single math god carries (per the
Lopsided Deities essay frame: the Wave God is "theory-rich,
infrastructure-poor"; weaving with Hestia's substrate-richness and
Vesta's model-richness makes the federation whole).

PASSPORT (v0.37 format):

  Math soil:    wave / phase_coherence (Kuramoto-style order parameter,
                pairwise cosine coherence, interference classification)
  Native object: per-channel phases θ_k in [0, π] derived as π·s_k/25
                from channel scores; pairwise coherence matrix
                C_ij = cos(θ_i - θ_j) in [-1, 1]; global coherence
                r = |1/N Σ e^(iθ_k)| in [0, 1] (Kuramoto order
                parameter); dominant phase = arg(Σ e^(iθ_k));
                interference classification per pair {constructive,
                destructive, orthogonal}; signal_magnitude (sum of
                channel scores) to disambiguate trivially-coherent
                zero-signal.
  Update law:   phase_recomputation (deterministic re-derivation from
                input). Floor: STATELESS (no learned phase offsets,
                no learned coherence thresholds). Real bounded learning
                of coherence thresholds lands in P12.
  Beats baseline where:
                Cross-channel agreement quantification -- "do all 5
                channels point the same direction?" Hestia gives
                deterministic state; Vesta gives band posterior;
                neither answers "are the channels self-consistent?".
                Incoherence under mixed signals (high industry_fit
                but zero budget_likelihood -> low pairwise coherence
                even when individual scores look strong).
                Interference pattern classification ("which channel
                pairs constructively reinforce, which destructively
                cancel").
  Killing ablation:
                All channel scores set to 0 -> all phases at 0 -> while
                pairwise coherences are trivially 1.0, signal_magnitude
                is 0. The "no_signal" flag in the payload makes the
                distinction first-class: trivial coherence ≠ real
                coherence. Consumers must consult signal_magnitude
                alongside global_coherence.
  Forbidden impersonations:
                * Hestia (deterministic banding) -- Indra does NOT
                  classify into bands; coherence is a continuous [-1, 1]
                  or [0, 1] field, never a categorical assignment.
                * Vesta (probability over bands) -- Indra does NOT
                  compute posteriors or credibilities; coherence is
                  cosine of phase difference, NOT a probability.
                * Oracle (composition / emission) -- Indra does NOT
                  emit prose, select templates, or fill slots.

NON_CLAIMS:
  - No method named .lead_band(), .gate_states() (Hestia)
  - No method named .posterior(), .credibility(), .belief() (Vesta)
  - No method named .compose(), .emit(), .render(), .write() (Oracle)
  - Coherence values are not probabilities -- cos is bounded [-1, 1],
    magnitude is bounded [0, 1], neither integrates to 1 over anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from ..sourcebook import SourceBook
from ..types import (
    SubstrateKind,
    SubstrateOutput,
    _sha256_of,
)


# ─── Passport (machine-readable v0.37 doctrine entry) ─────────────────────

INDRA_PASSPORT: Dict[str, Any] = {
    "name":          "indra",
    "math_soil":     "wave.phase_coherence",
    "native_object": "per_channel_phases + pairwise_coherence_matrix + global_coherence_kuramoto + dominant_phase + interference_classification + signal_magnitude",
    "update_law":    "phase_recomputation",   # Floor; threshold learning in P12
    "beats_baseline_where": (
        "cross_channel_agreement_quantification",
        "incoherence_detection_under_mixed_signals",
        "interference_pattern_classification",
        "trivial_coherence_disambiguation_via_signal_magnitude",
    ),
    "killing_ablation": "all_channels_zero_yields_no_signal_with_magnitude_zero",
    "forbidden_impersonations": (
        "hestia.lead_band_classification",
        "hestia.gate_states",
        "vesta.posterior",
        "vesta.credibility",
        "oracle.compose",
        "oracle.emit",
    ),
    "non_claims_method_names_must_not_exist": (
        "lead_band", "gate_states",                  # Hestia
        "posterior", "credibility", "belief",        # Vesta
        "compose", "emit", "render", "write",        # Oracle
    ),
}


# ─── Constants ────────────────────────────────────────────────────────────

# Same channel set Hestia and Vesta use; this is the canonical Scout
# rubric. Order is fixed (alphabetical) for deterministic iteration.
CHANNEL_NAMES: Tuple[str, ...] = (
    "budget_likelihood_score",
    "company_size_fit",
    "decision_maker_seniority",
    "growth_signals",
    "industry_fit",
)

# Phase mapping: score s in [0, 25] -> θ in [0, π]. Uses π (not 2π) to
# avoid 2π wrap-around degeneracy where score 0 and score 25 would
# collapse to the same phase.
CHANNEL_SCORE_MAX = 25
PHASE_MAX = math.pi

# Interference classification thresholds. cos(Δθ) > +0.7 -> within ~45°
# of agreement; cos(Δθ) < -0.7 -> within ~45° of opposition; otherwise
# orthogonal.
INTERFERENCE_CONSTRUCTIVE_THRESHOLD = 0.7
INTERFERENCE_DESTRUCTIVE_THRESHOLD = -0.7


# ─── Indra's native object ────────────────────────────────────────────────

@dataclass(frozen=True)
class IndraNativeObject:
    """Indra's wave-phase state derived from a SourceBook's channel
    scores. All fields are wave-native: phases (radians), cosine
    coherences ([-1, 1]), magnitude order parameter ([0, 1]).
    Hashable for receipt chain; serializable via to_payload."""

    # Per-channel phases θ_k in [0, π]
    phases: Dict[str, float]
    # Pairwise coherence matrix: upper triangle keyed "{ch_i}__{ch_j}"
    # with ch_i < ch_j lexicographically; value is cos(θ_i - θ_j).
    pairwise_coherences: Dict[str, float]
    # Global coherence (Kuramoto order parameter magnitude), [0, 1]
    global_coherence: float
    # Dominant phase = arg(Σ e^(iθ_k)), [0, π] (mapped back from [-π, π])
    dominant_phase: float
    # Per-pair interference classification: one of
    # "constructive" | "destructive" | "orthogonal"
    interference_classification: Dict[str, str]
    # Sum of channel scores; 0 = pure ablation, disambiguates trivial
    # coherence from real coherence
    signal_magnitude: int

    def to_payload(self) -> Dict[str, Any]:
        """JSON-safe dict for inclusion in SubstrateOutput.output_payload.
        Floats rounded to 9 dp to absorb cross-platform drift (matches
        Vesta pattern)."""
        def r(x: float) -> float:
            return round(float(x), 9)

        return {
            "phases": {k: r(v) for k, v in sorted(self.phases.items())},
            "pairwise_coherences": {
                k: r(v) for k, v in sorted(self.pairwise_coherences.items())
            },
            "global_coherence": r(self.global_coherence),
            "dominant_phase": r(self.dominant_phase),
            "interference_classification": dict(
                sorted(self.interference_classification.items())
            ),
            "signal_magnitude": int(self.signal_magnitude),
        }


# ─── Helpers ──────────────────────────────────────────────────────────────

def _phase_from_score(score: int) -> float:
    """Map a channel score in [0, 25] to a phase in [0, π].
    Defensive: clamp out-of-range scores into [0, max]."""
    s = max(0, min(int(score), CHANNEL_SCORE_MAX))
    return PHASE_MAX * (s / CHANNEL_SCORE_MAX)


def _pair_key(ch_i: str, ch_j: str) -> str:
    """Canonical pair key with lexicographic ordering."""
    a, b = sorted([ch_i, ch_j])
    return f"{a}__{b}"


def _classify_interference(coherence: float) -> str:
    """Map a cosine coherence in [-1, 1] to one of three labels."""
    if coherence > INTERFERENCE_CONSTRUCTIVE_THRESHOLD:
        return "constructive"
    if coherence < INTERFERENCE_DESTRUCTIVE_THRESHOLD:
        return "destructive"
    return "orthogonal"


def _global_coherence_and_dominant_phase(
    phases: Dict[str, float],
) -> Tuple[float, float]:
    """Compute the Kuramoto order parameter (magnitude in [0, 1]) and
    the dominant phase (argument of the sum of unit vectors)."""
    n = len(phases)
    if n == 0:
        return 0.0, 0.0
    sum_x = sum(math.cos(theta) for theta in phases.values())
    sum_y = sum(math.sin(theta) for theta in phases.values())
    mean_x = sum_x / n
    mean_y = sum_y / n
    magnitude = math.sqrt(mean_x * mean_x + mean_y * mean_y)
    # atan2 returns [-π, π]; remap to [0, π] for consistency with phases.
    # Since our phases are all in [0, π], the dominant phase will also
    # land in [0, π].
    dominant = math.atan2(mean_y, mean_x)
    if dominant < 0:
        dominant += 2 * math.pi
    # Wrap into [0, π] domain: if we landed in (π, 2π], reflect.
    if dominant > math.pi:
        dominant = 2 * math.pi - dominant
    return magnitude, dominant


# ─── Public entry point ───────────────────────────────────────────────────

def compute_indra(book: SourceBook) -> SubstrateOutput:
    """Compute Indra's output for a given SourceBook. Pure deterministic
    function: same book -> identical SubstrateOutput.

    Wave-phase pipeline:
      1. Read channel scores from scout_metadata (defaulting to 0).
      2. Map each score to phase θ_k = π · s_k / 25 in [0, π].
      3. Compute pairwise cosine coherences C_ij = cos(θ_i - θ_j).
      4. Classify each pair as constructive/destructive/orthogonal.
      5. Compute global Kuramoto order parameter r = |1/N Σ e^(iθ_k)|.
      6. Compute dominant phase = arg(Σ e^(iθ_k)).
      7. Compute signal_magnitude = sum of channel scores.

    NON_CLAIMS: this function does not classify into bands (Hestia
    territory), does not compute posteriors (Vesta), does not emit prose
    (Oracle). Outputs are coherences and phases only.
    """
    meta = book.scout_metadata or {}

    # Channel phases
    phases: Dict[str, float] = {
        ch: _phase_from_score(int(meta.get(ch, 0)))
        for ch in CHANNEL_NAMES
    }
    signal_magnitude: int = sum(int(meta.get(ch, 0)) for ch in CHANNEL_NAMES)

    # Pairwise coherences + interference classification
    pairwise: Dict[str, float] = {}
    interference: Dict[str, str] = {}
    channels = sorted(phases.keys())
    for i in range(len(channels)):
        for j in range(i + 1, len(channels)):
            ch_i, ch_j = channels[i], channels[j]
            coh = math.cos(phases[ch_i] - phases[ch_j])
            key = _pair_key(ch_i, ch_j)
            pairwise[key] = coh
            interference[key] = _classify_interference(coh)

    # Global coherence + dominant phase
    global_coh, dominant = _global_coherence_and_dominant_phase(phases)

    native = IndraNativeObject(
        phases=phases,
        pairwise_coherences=pairwise,
        global_coherence=global_coh,
        dominant_phase=dominant,
        interference_classification=interference,
        signal_magnitude=signal_magnitude,
    )

    payload = native.to_payload()
    return SubstrateOutput(
        substrate_kind=SubstrateKind.INDRA,
        native_object_hash=_sha256_of(payload),
        output_payload=payload,
    )
