"""Anvil-Scout — B2B lead intelligence (structural, deterministic, bounded-cognition)."""

__version__ = "0.1.0-TB10"
__taskbook__ = "TB-10 — partner release"

from anvil_scout.contracts import ScrapedInput, ScoredOutput, SignalEvidence

__all__ = ["ScrapedInput", "ScoredOutput", "SignalEvidence", "__version__"]
