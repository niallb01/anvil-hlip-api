"""Anvil-Pantheon-Floor — Vesta substrate organ (Packet 6).

Vesta owns the PROBABILITY/BAYESIAN math soil. Second substrate organ;
weaves with Hestia (machine/deterministic) and Indra (wave/phase) to
form the three-substrate federation that cancels the lopsidedness of
any single math god.

PASSPORT (v0.37 format):

  Math soil:    probability / bayesian (explicit prior, likelihood,
                posterior; no learned weights at floor)
  Native object: posterior distribution P(lead_band | evidence) over
                {low, medium, high}; per-channel Beta(alpha, beta)
                credibility with mean as the shrunken score estimate;
                MAP band; posterior entropy as uncertainty observable.
  Update law:   Bayesian update via per-evidence band-conditional
                Bernoulli likelihoods. Floor: STATELESS (fully
                re-computed from input each call). Bounded-update with
                Beta-conjugate posteriors over outcomes lands in P12.
  Beats baseline where:
                Uncertainty matters: which lead band the evidence
                actually supports, with what residual entropy. A
                deterministic classifier (Hestia) cannot answer
                "how confident are we?"; Vesta makes the uncertainty
                a first-class field.
  Killing ablation:
                Empty SourceBook (no V/W/M evidence) -> posterior
                collapses to UNIFORM (1/3, 1/3, 1/3) over bands.
                Confirms the substrate is consuming actual evidence
                and not faking concentration.
  Forbidden impersonations:
                * Hestia (deterministic banding via score cuts) --
                  Vesta does NOT produce a single deterministic
                  lead_band classification from channel scores. It
                  produces a POSTERIOR; the MAP is derived FROM the
                  posterior, not from scores.
                * Indra (wave/phase coherence across channels) --
                  Vesta does NOT compute cross-channel agreement,
                  coherence, or interference.
                * Oracle (composition / emission) -- Vesta does NOT
                  emit prose, select templates, or fill slots.

NON_CLAIMS (impersonation-forbidding discipline):
  - No method named .lead_band() or .gate_states() (Hestia)
  - No method named .coherence() / .interference() / .agreement() (Indra)
  - No method named .compose() / .emit() / .render() / .write() (Oracle)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict

from ..sourcebook import SourceBook
from ..types import (
    EvidenceKind,
    SubstrateKind,
    SubstrateOutput,
    _sha256_of,
)


# ─── Passport (machine-readable v0.37 doctrine entry) ─────────────────────

VESTA_PASSPORT: Dict[str, Any] = {
    "name":          "vesta",
    "math_soil":     "probability.bayesian",
    "native_object": "posterior_over_lead_band + per_channel_beta_credibility + map_band + posterior_entropy",
    "update_law":    "bayesian_likelihood_update",   # Floor; conjugate-bounded in P12
    "beats_baseline_where": (
        "lead_band_uncertainty_quantification",
        "posterior_entropy_as_observable",
        "per_channel_beta_credibility_with_shrinkage",
        "map_estimate_derived_from_posterior",
    ),
    "killing_ablation": "empty_book_yields_uniform_posterior",
    "forbidden_impersonations": (
        "hestia.lead_band_classification",
        "hestia.gate_states",
        "indra.coherence",
        "indra.interference",
        "indra.agreement",
        "oracle.compose",
        "oracle.emit",
    ),
    "non_claims_method_names_must_not_exist": (
        "lead_band", "gate_states",                      # Hestia
        "coherence", "interference", "agreement",        # Indra
        "compose", "emit", "render", "write",            # Oracle
    ),
}


# ─── Bayesian model parameters (the LIKELIHOOD function) ──────────────────

# Per-evidence band-conditional Bernoulli likelihoods. These are the
# "observation model": given the true lead_band, with what probability
# do we expect to see one VERIFIED/WEAK/MISSING evidence card?
#
# Interpretation:
#  - high-band leads tend to have many verified spans (positive evidence)
#  - low-band leads tend to have many missing spans (negative evidence)
#  - weak evidence is ambiguous (peaks at medium)
#
# These probabilities sum to 1 across each row (over bands) under a
# per-card observation independence assumption. The numbers are
# stipulated at floor; outcome calibration tunes them in Packet 12.
EVIDENCE_LIKELIHOODS: Dict[EvidenceKind, Dict[str, float]] = {
    EvidenceKind.VERIFIED: {"low": 0.1,  "medium": 0.3, "high": 0.6},
    EvidenceKind.WEAK:     {"low": 0.25, "medium": 0.5, "high": 0.25},
    EvidenceKind.MISSING:  {"low": 0.6,  "medium": 0.3, "high": 0.1},
}

# Uniform prior over bands -- before any evidence, all bands are equally
# likely. This is what the killing ablation must reproduce.
UNIFORM_PRIOR: Dict[str, float] = {"low": 1.0 / 3.0, "medium": 1.0 / 3.0, "high": 1.0 / 3.0}

# Channels carried as native fields with Beta credibility
CHANNEL_NAMES = (
    "industry_fit",
    "company_size_fit",
    "decision_maker_seniority",
    "budget_likelihood_score",
    "growth_signals",
)

# Scout rubric: each channel score is in [0, 25]. Used as the "trials"
# parameter for Beta(score+1, 25-score+1) with Laplace smoothing.
CHANNEL_SCORE_MAX = 25


# ─── Vesta's native object ────────────────────────────────────────────────

@dataclass(frozen=True)
class VestaNativeObject:
    """Vesta's Bayesian state derived from a SourceBook. All fields are
    probability-native: posteriors, Beta credibilities, entropy.
    Hashable for receipt chain; serializable via to_payload."""

    # Posterior distribution over {low, medium, high}; sums to 1.
    posterior: Dict[str, float]
    # MAP band: argmax of posterior. Derived FROM posterior, never
    # from raw channel scores (that would be Hestia's impersonation).
    map_band: str
    # Posterior entropy in nats; range [0, log(3)]. Lower = more
    # concentrated, higher = more uncertain.
    posterior_entropy: float
    # Per-channel Beta credibility: alpha = score+1, beta = (max-score)+1,
    # mean = alpha/(alpha+beta). The mean is the shrunken score estimate
    # (Laplace-smoothed toward 0.5).
    channel_credibility: Dict[str, Dict[str, float]]
    # Counts that fed the likelihood (carried for receipt readability)
    evidence_counts: Dict[str, int]

    def to_payload(self) -> Dict[str, Any]:
        """JSON-safe dict for inclusion in SubstrateOutput.output_payload.
        Floats are rounded to 9 decimal places to absorb cross-platform
        float drift without losing meaningful resolution."""
        def r(x: float) -> float:
            return round(float(x), 9)

        return {
            "posterior": {b: r(p) for b, p in sorted(self.posterior.items())},
            "map_band": self.map_band,
            "posterior_entropy": r(self.posterior_entropy),
            "channel_credibility": {
                ch: {k: r(v) for k, v in sorted(cred.items())}
                for ch, cred in sorted(self.channel_credibility.items())
            },
            "evidence_counts": dict(sorted(self.evidence_counts.items())),
        }


# ─── Helpers ──────────────────────────────────────────────────────────────

def _log_posterior(verified: int, weak: int, missing: int) -> Dict[str, float]:
    """Compute log-posterior over bands using log-sum-exp for numerical
    stability. Returns un-normalized log-posterior dict."""
    log_post: Dict[str, float] = {b: math.log(UNIFORM_PRIOR[b]) for b in UNIFORM_PRIOR}

    for ev_kind, n in (
        (EvidenceKind.VERIFIED, verified),
        (EvidenceKind.WEAK, weak),
        (EvidenceKind.MISSING, missing),
    ):
        if n == 0:
            continue
        liks = EVIDENCE_LIKELIHOODS[ev_kind]
        for b in log_post:
            log_post[b] += n * math.log(liks[b])

    return log_post


def _normalize_posterior(log_post: Dict[str, float]) -> Dict[str, float]:
    """Normalize a log-posterior via log-sum-exp. Returns probabilities
    summing to 1 within float epsilon. JB-P6-3 numerical stability."""
    max_lp = max(log_post.values())
    unnormalized = {b: math.exp(log_post[b] - max_lp) for b in log_post}
    z = sum(unnormalized.values())
    return {b: u / z for b, u in unnormalized.items()}


def _posterior_entropy(posterior: Dict[str, float]) -> float:
    """Shannon entropy in nats. Zero-probability bands contribute 0
    (limit of p*log(p) as p->0+)."""
    return -sum(p * math.log(p) for p in posterior.values() if p > 0)


def _beta_credibility(score: int) -> Dict[str, float]:
    """Per-channel Beta(score+1, max-score+1) with Laplace smoothing.
    Returns alpha, beta, mean. The mean is the shrunken score estimate
    on [0, 1] -- a true Bayesian alternative to dividing score by max."""
    # Clamp score into [0, max] to keep alpha, beta > 0
    s = max(0, min(int(score), CHANNEL_SCORE_MAX))
    alpha = float(s + 1)
    beta = float(CHANNEL_SCORE_MAX - s + 1)
    mean = alpha / (alpha + beta)
    return {"alpha": alpha, "beta": beta, "mean": mean}


# Tie-break order for MAP under tied posteriors. Conservative:
# under-calling a lead is less commercially costly than over-calling it.
_MAP_TIE_BREAK_ORDER = ("low", "medium", "high")


def _conservative_map_band(posterior: Dict[str, float]) -> str:
    """Argmax of posterior with conservative tie-break (prefers low
    over medium over high). JB-P6 discipline: when posterior is uniform
    or near-uniform (entropy near log(3)), the MAP is meaningless and
    the Oracle should consult posterior_entropy; this function only
    guarantees the MAP is a well-defined function of the posterior."""
    max_p = max(posterior.values())
    for preferred in _MAP_TIE_BREAK_ORDER:
        if preferred in posterior and posterior[preferred] == max_p:
            return preferred
    # Defensive fallback (should be unreachable)
    return min(posterior.keys())


# ─── Public entry point ───────────────────────────────────────────────────

def compute_vesta(book: SourceBook) -> SubstrateOutput:
    """Compute Vesta's output for a given SourceBook. Pure deterministic
    function: same book -> identical SubstrateOutput.

    Bayesian pipeline:
      1. Start with uniform prior over {low, medium, high}.
      2. Apply per-evidence band-conditional likelihoods for each
         VERIFIED/WEAK/MISSING card in the book.
      3. Normalize via log-sum-exp to get posterior.
      4. Derive MAP band from posterior (NOT from channel scores).
      5. Compute posterior entropy as uncertainty observable.
      6. Compute per-channel Beta(score+1, max-score+1) credibility.

    NON_CLAIMS: this function does not produce a deterministic lead_band
    classification, does not compute coherence, does not emit prose.
    The MAP band is the argmax of the Bayesian posterior, not a
    score-cut classification.
    """
    # Evidence counts from the book
    verified = len(book.by_evidence_kind(EvidenceKind.VERIFIED))
    weak = len(book.by_evidence_kind(EvidenceKind.WEAK))
    missing = len(book.by_evidence_kind(EvidenceKind.MISSING))

    # Posterior over bands
    log_post = _log_posterior(verified, weak, missing)
    posterior = _normalize_posterior(log_post)

    # MAP band derived FROM posterior. Tie-break is CONSERVATIVE:
    # prefer "low" then "medium" then "high" when posteriors are equal.
    # Rationale: in commercial use, falsely calling a lead "high" is a
    # bigger downside (wasted sales effort) than falsely calling it
    # "low". Under genuine uncertainty (e.g., uniform posterior, when
    # posterior_entropy approaches log(3)), the MAP is essentially
    # meaningless and the Oracle should refuse using posterior_entropy
    # as the signal -- the MAP value here is only there to be a
    # well-defined function of the posterior.
    map_band = _conservative_map_band(posterior)

    # Posterior entropy
    entropy = _posterior_entropy(posterior)

    # Per-channel Beta credibility
    meta = book.scout_metadata or {}
    channel_credibility = {
        ch: _beta_credibility(int(meta.get(ch, 0)))
        for ch in CHANNEL_NAMES
    }

    native = VestaNativeObject(
        posterior=posterior,
        map_band=map_band,
        posterior_entropy=entropy,
        channel_credibility=channel_credibility,
        evidence_counts={"verified": verified, "weak": weak, "missing": missing},
    )

    payload = native.to_payload()
    return SubstrateOutput(
        substrate_kind=SubstrateKind.VESTA,
        native_object_hash=_sha256_of(payload),
        output_payload=payload,
    )
