"""I/O contracts for Anvil-Scout.

These are the only data shapes that cross the public boundary.
Internal modules may add their own structures, but the input/output
contract is fixed by the JSON schema.
"""

from dataclasses import dataclass, field, asdict
from typing import List


# ─────────────────────────────────────────────────────────────────────────────
# INPUT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScrapedInput:
    """One lead + scraped website content. Matches partner's n8n shape."""

    name: str
    title: str
    company: str
    website_url: str
    website_content: str

    @classmethod
    def from_dict(cls, d: dict) -> "ScrapedInput":
        return cls(
            name=d.get("name", ""),
            title=d.get("title", ""),
            company=d.get("company", ""),
            website_url=d.get("website_url", ""),
            website_content=d.get("website_content", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalEvidence:
    """Signal extraction record. Shape matches SCHEMA.json.

    `signal_density` (renamed from `confidence` at TB-17B): a structural
    ratio of corroborating evidence found in the input — verified count
    plus half the weak count, divided by total spans, clamped to
    [0.2, 0.8]. NOT a probability. For the calibrated conversion
    probability, see `ScoredOutput.predicted_quality` (TB-17A).
    """

    verified: List[str] = field(default_factory=list)
    weak: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    signal_density: float = 0.0
    thin_scrape: bool = False


@dataclass
class ScoredOutput:
    """Scoring result. Shape matches SCHEMA.json (drop-in compatible with partner's prompt)."""

    lead_score: int = 0
    industry_fit: int = 0
    company_size_fit: int = 0
    decision_maker_seniority: int = 0
    budget_likelihood_score: int = 0
    growth_signals: int = 0
    pain_points: List[str] = field(default_factory=list)
    budget_likelihood: str = "low"      # "high" | "medium" | "low"
    decision_maker: bool = False
    predicted_quality: float = 0.5      # TB-17A: calibrated conversion probability
    rationale: str = ""
    signal_evidence: SignalEvidence = field(default_factory=SignalEvidence)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d
