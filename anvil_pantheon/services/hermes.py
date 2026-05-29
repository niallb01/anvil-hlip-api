"""Anvil-Pantheon-Floor — Hermes service organ (Packet 8).

Hermes is a SERVICE organ (no math soil). Its role is plumbing: it
bundles the three substrate outputs (Hestia / Vesta / Indra) for the
Oracle, computes inter-substrate agreement, and exposes a uniform
summary interface so the Oracle can consume bundles without importing
substrate-specific modules.

NON_CLAIMS (the service-organ discipline):
  - Does NOT compute substrate outputs (Hestia/Vesta/Indra own those)
  - Does NOT classify into bands itself (the only "band" Hermes
    surfaces is the one substrates already produced)
  - Does NOT emit prose or select templates (Oracle, P10)
  - Does NOT compute coherence or posterior of its own (would impersonate
    substrates)

Floor scope:
  - SubstrateBundle: frozen dataclass holding the three SubstrateOutputs
  - Construction is strict: requires all three substrates present with
    the correct substrate_kind tag (raises on missing or wrong-kind)
  - band_agreement: Hestia.lead_band == Vesta.map_band (booleans only;
    no synthesis)
  - .summary(): uniform dict exposing band, vesta_confidence,
    signal_magnitude, global_coherence -- enough for the Oracle to
    decide emit/refuse without needing Hestia/Vesta/Indra imports

Floor disclaimer: Indra does NOT produce a band (correctly, per its
passport -- that would be Hestia impersonation). So band_agreement is
2-way (Hestia + Vesta). Indra contributes TWO orthogonal gates of its
own instead of a band vote: signal_magnitude (has_real_signal -- "is
there evidence at all?") and global_coherence (has_coherent_signal --
"does the evidence agree with itself?"). The Oracle consults both.
This is three independent verdicts on three axes (law / calibration /
coherence), not three votes on one band.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict

from ..types import SubstrateKind, SubstrateOutput


# ─── Constants ────────────────────────────────────────────────────────────

# Hermes preserves substrate independence by exposing ONLY these uniform
# field names through .summary(). Adding a new field is a conscious
# decision -- update the docstring and tests if you do.
SUMMARY_FIELDS = (
    "hestia_band",
    "vesta_map_band",
    "vesta_posterior_entropy",
    "indra_signal_magnitude",
    "indra_global_coherence",
    "band_agreement",
)

# Threshold above which Indra's signal_magnitude is considered
# "real signal" rather than "trivially coherent ablation case".
# Below this, consumers should treat Indra's coherence as untrustworthy.
SIGNAL_MAGNITUDE_FLOOR = 10

# Floor on Indra's global_coherence (Kuramoto order parameter, [0, 1]).
# Reached ONLY when signal_magnitude is already above SIGNAL_MAGNITUDE_FLOOR
# -- i.e. there IS real signal, but we additionally require the channels to
# AGREE in phase before trusting a verdict. Catches the "strong but
# self-contradictory" case (e.g. budget_likelihood=0 while industry_fit=25)
# that the magnitude floor and the Hestia/Vesta band vote both miss.
#
# Default is deliberately CONSERVATIVE (loose): it refuses only clearly
# incoherent evidence and leaves a wide margin below the calibrated emit
# band. Tighten over time via bounded_learning against real outcomes, or
# override per-run with the ANVIL_COHERENCE_FLOOR environment variable
# (no config-file edits required).
COHERENCE_FLOOR = float(os.environ.get("ANVIL_COHERENCE_FLOOR", "0.5"))


# ─── Bundle ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SubstrateBundle:
    """The three substrate outputs bundled for the Oracle. Construction
    via bundle_substrates() enforces presence and kind correctness."""
    hestia_out: SubstrateOutput
    vesta_out: SubstrateOutput
    indra_out: SubstrateOutput
    source_book_hash: str

    def summary(self) -> Dict[str, Any]:
        """Uniform view for the Oracle. Returns ONLY the fields listed
        in SUMMARY_FIELDS -- no raw substrate payloads, no
        substrate-specific keys. This is what preserves substrate
        independence (JB-P8-5)."""
        hp = self.hestia_out.output_payload
        vp = self.vesta_out.output_payload
        ip = self.indra_out.output_payload

        hestia_band = hp.get("lead_band", "")
        vesta_band = vp.get("map_band", "")

        return {
            "hestia_band":             hestia_band,
            "vesta_map_band":          vesta_band,
            "vesta_posterior_entropy": vp.get("posterior_entropy", 0.0),
            "indra_signal_magnitude":  ip.get("signal_magnitude", 0),
            "indra_global_coherence":  ip.get("global_coherence", 0.0),
            "band_agreement":          (hestia_band == vesta_band) and bool(hestia_band),
        }

    def has_real_signal(self) -> bool:
        """True iff Indra reports signal_magnitude above the floor (i.e.
        not a trivially-coherent ablation case). Below the floor, the
        Oracle should not trust the substrate verdicts blindly."""
        sm = self.indra_out.output_payload.get("signal_magnitude", 0)
        return sm > SIGNAL_MAGNITUDE_FLOOR

    def has_coherent_signal(self) -> bool:
        """True iff Indra reports global_coherence at or above the floor.

        This is Indra's OWN verdict ("do the channels agree in phase?"),
        distinct from the magnitude floor (has_real_signal) and from the
        Hestia/Vesta band vote. It is meaningful only when there IS signal:
        an empty/thin book is trivially coherent (coherence == 1.0), so this
        check must be gated behind has_real_signal() -- the Oracle calls it
        in that order. The failure mode it catches is "real signal present,
        bands agree, but the evidence contradicts itself" -- which neither
        the magnitude floor nor band-agreement can see."""
        coh = self.indra_out.output_payload.get("global_coherence", 0.0)
        return coh >= COHERENCE_FLOOR


# ─── Bundle constructor (entry point) ─────────────────────────────────────

def bundle_substrates(
    hestia_out: SubstrateOutput,
    vesta_out: SubstrateOutput,
    indra_out: SubstrateOutput,
    source_book_hash: str,
) -> SubstrateBundle:
    """Construct a SubstrateBundle from three substrate outputs.

    JB-P8-4 discipline: this function REQUIRES all three substrates with
    the correct substrate_kind tag. Missing or wrong-kind inputs raise
    ValueError -- silent dropping is forbidden.

    Raises:
        ValueError: if any input is None, has wrong substrate_kind, or
            source_book_hash is empty.
    """
    if hestia_out is None or vesta_out is None or indra_out is None:
        raise ValueError(
            "bundle_substrates requires all three substrates; "
            f"got hestia={hestia_out!r}, vesta={vesta_out!r}, indra={indra_out!r}"
        )

    if hestia_out.substrate_kind != SubstrateKind.HESTIA:
        raise ValueError(
            f"hestia_out has wrong substrate_kind: {hestia_out.substrate_kind}"
        )
    if vesta_out.substrate_kind != SubstrateKind.VESTA:
        raise ValueError(
            f"vesta_out has wrong substrate_kind: {vesta_out.substrate_kind}"
        )
    if indra_out.substrate_kind != SubstrateKind.INDRA:
        raise ValueError(
            f"indra_out has wrong substrate_kind: {indra_out.substrate_kind}"
        )

    if not source_book_hash:
        raise ValueError("source_book_hash must be a non-empty string")

    return SubstrateBundle(
        hestia_out=hestia_out,
        vesta_out=vesta_out,
        indra_out=indra_out,
        source_book_hash=source_book_hash,
    )
