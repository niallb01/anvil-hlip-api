"""Anvil-Pantheon-Floor — Mnemosyne service organ (Packet 8).

Mnemosyne is a SERVICE organ (not a substrate organ -- no math soil).
Service organs are the plumbing layer of the Pantheon: they carry
state, route messages, and provide query interfaces without computing
substrate-level claims themselves.

Mnemosyne's role: carry the chain of EmissionCertificates over time,
provide query interfaces over past receipts, and detect lead-quality
drift via chi-square-flavored comparison of band distributions across
two consecutive windows.

NON_CLAIMS (the service-organ discipline):
  - Does NOT compute substrate outputs (Hestia/Vesta/Indra own those)
  - Does NOT modify past receipts (append-only; JB-P8-1)
  - Does NOT emit prose or select templates (Oracle, P10)
  - Drift detection is a SIGNAL for monitoring, not a refusal trigger
    (the Oracle decides what to do with the drift verdict)

Floor scope:
  - Thin wrapper over P2's ReceiptStore
  - Band-distribution aggregation (count by Hestia's lead_band per window)
  - Drift check: chi-square-flavored distance between two windows;
    fires only when total sample size in both windows meets the
    MIN_SAMPLES floor
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..receipts import ReceiptStore, validate_certificate
from ..types import CertificationStatus, EmissionCertificate


# ─── Drift detection constants ────────────────────────────────────────────

# Below this total across both windows, drift_check returns
# "insufficient_data" rather than firing. Floor heuristic; tuned in P12.
MIN_SAMPLES_FOR_DRIFT = 10

# Chi-square-flavored threshold for drift detection. Values above this
# trigger drift_detected.
DRIFT_THRESHOLD = 0.30

# Bands we track (must match Hestia + Vesta band vocabulary)
KNOWN_BANDS: Tuple[str, ...] = ("low", "medium", "high")
KNOWN_OUTCOMES: Tuple[str, ...] = ("low", "medium", "high", "refused")


# ─── Verdict types ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BandDistribution:
    """Counts and proportions over outcomes across a window of receipts."""
    counts: Dict[str, int]
    total: int

    def proportion(self, outcome: str) -> float:
        if self.total == 0:
            return 0.0
        return self.counts.get(outcome, 0) / self.total


@dataclass(frozen=True)
class DriftVerdict:
    """Result of drift_check.
      - status: "clean" | "drift_detected" | "insufficient_data"
      - metric: chi-square-flavored distance (None if insufficient_data)
      - prior_dist / recent_dist: the two windows compared
      - threshold: the metric threshold used (carried for forensic review)
    """
    status: str
    metric: Optional[float]
    prior_dist: BandDistribution
    recent_dist: BandDistribution
    threshold: float


# ─── Outcome extraction (band or refused) ─────────────────────────────────

def _outcome_for(cert: EmissionCertificate) -> str:
    """Extract the realized outcome from a certificate:
      - "low" | "medium" | "high" from hestia.lead_band if substrate
        present AND slot_fills indicate emission happened
      - "refused" if slot_fills is empty or all REFUSED

    Defensive: returns "refused" on missing/malformed substrate output."""
    # Refused if no slots filled, or every slot was REFUSED
    if not cert.slot_fills:
        return "refused"
    if all(s.certification == CertificationStatus.REFUSED for s in cert.slot_fills):
        return "refused"

    # Otherwise read Hestia's lead_band
    hestia = cert.substrate_outputs.get("hestia")
    if hestia is None:
        return "refused"
    band = (hestia.output_payload.get("lead_band") or "").lower()
    if band in KNOWN_BANDS:
        return band
    return "refused"


# ─── Mnemosyne store ──────────────────────────────────────────────────────

class MnemosyneStore:
    """Wraps a ReceiptStore from P2; adds Mnemosyne-specific queries
    and drift detection. The underlying store is the source of truth;
    Mnemosyne never mutates past receipts."""

    def __init__(self, receipt_store: ReceiptStore):
        self._store = receipt_store

    # ─── Append (delegates to underlying store) ────────────────────────

    def record(self, certificate: EmissionCertificate) -> bool:
        """Append a new certificate to the underlying chain. Returns
        True on append, False if the certificate_id already existed
        (idempotent re-banking). Raises ValueError if the certificate
        fails structural validation."""
        ok, errors = validate_certificate(certificate)
        if not ok:
            raise ValueError(f"refusing to record invalid certificate: {errors}")
        return self._store.append_idempotent(certificate)

    # ─── Read APIs (no mutation) ───────────────────────────────────────

    def count(self) -> int:
        return len(self._store.load_all())

    def all_certificates(self) -> List[EmissionCertificate]:
        return list(self._store.load_all())

    def recent(self, n: int) -> List[EmissionCertificate]:
        """Return the most recent n certificates (newest first by chain
        position)."""
        if n < 0:
            raise ValueError(f"n must be non-negative, got {n}")
        all_certs = self._store.load_all()
        return list(reversed(all_certs[-n:])) if n > 0 else []

    def since(self, timestamp_iso: str) -> List[EmissionCertificate]:
        """All certificates emitted at or after the given ISO timestamp.
        Chronological order (oldest first)."""
        return [c for c in self._store.load_all() if c.timestamp >= timestamp_iso]

    # ─── Distribution aggregation ──────────────────────────────────────

    def count_by_band(
        self,
        certificates: Optional[List[EmissionCertificate]] = None,
    ) -> BandDistribution:
        """Aggregate the given certificates by their realized outcome
        (low/medium/high/refused). If certificates is None, uses
        all_certificates()."""
        if certificates is None:
            certificates = self.all_certificates()

        counts: Dict[str, int] = {o: 0 for o in KNOWN_OUTCOMES}
        for c in certificates:
            outcome = _outcome_for(c)
            counts[outcome] = counts.get(outcome, 0) + 1

        return BandDistribution(counts=counts, total=len(certificates))

    # ─── Drift detection ───────────────────────────────────────────────

    def drift_check(self, recent_window: int, prior_window: int) -> DriftVerdict:
        """Compare band distribution of the most-recent `recent_window`
        certificates vs the `prior_window` immediately preceding them.

        Returns DriftVerdict with status in {clean, drift_detected,
        insufficient_data}. Metric is chi-square-flavored distance.
        """
        if recent_window <= 0 or prior_window <= 0:
            raise ValueError(
                f"window sizes must be positive; got recent={recent_window} "
                f"prior={prior_window}"
            )

        all_certs = self._store.load_all()
        total_needed = recent_window + prior_window

        if len(all_certs) < total_needed:
            recent_slice = all_certs[-recent_window:] if recent_window <= len(all_certs) else all_certs
            return DriftVerdict(
                status="insufficient_data",
                metric=None,
                prior_dist=self.count_by_band([]),
                recent_dist=self.count_by_band(recent_slice),
                threshold=DRIFT_THRESHOLD,
            )

        recent_slice = all_certs[-recent_window:]
        prior_slice = all_certs[-(recent_window + prior_window): -recent_window]

        prior_dist = self.count_by_band(prior_slice)
        recent_dist = self.count_by_band(recent_slice)

        if prior_dist.total + recent_dist.total < MIN_SAMPLES_FOR_DRIFT:
            return DriftVerdict(
                status="insufficient_data",
                metric=None,
                prior_dist=prior_dist,
                recent_dist=recent_dist,
                threshold=DRIFT_THRESHOLD,
            )

        metric = _chi_square_flavored(prior_dist, recent_dist)
        status = "drift_detected" if metric > DRIFT_THRESHOLD else "clean"

        return DriftVerdict(
            status=status,
            metric=metric,
            prior_dist=prior_dist,
            recent_dist=recent_dist,
            threshold=DRIFT_THRESHOLD,
        )


# ─── Helpers ──────────────────────────────────────────────────────────────

_EPS = 1e-9


def _chi_square_flavored(prior: BandDistribution, recent: BandDistribution) -> float:
    """Chi-square-flavored distance between two band distributions.

    metric = Σ_o (p_recent(o) - p_prior(o))^2 / (p_prior(o) + ε)

    Bounded above by ~2 for fully disjoint distributions."""
    total = 0.0
    for o in KNOWN_OUTCOMES:
        p_prior = prior.proportion(o)
        p_recent = recent.proportion(o)
        diff = p_recent - p_prior
        total += (diff * diff) / (p_prior + _EPS)
    return total
