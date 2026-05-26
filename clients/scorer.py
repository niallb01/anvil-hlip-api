import json
import logging
from dataclasses import dataclass, field, asdict
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class ScrapedInput:
    name: str
    website_url: str
    website_content: str
    title: str
    company: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoredOutput:
    lead_score: int = 0
    industry_fit: int = 0
    company_size_fit: int = 0
    decision_maker_seniority: int = 0
    budget_likelihood_score: int = 0
    growth_signals: int = 0
    pain_points: List[str] = field(default_factory=list)
    budget_likelihood: str = "low"
    decision_maker: bool = False
    rationale: str = ""
    signal_evidence: dict = field(default_factory=dict)


def _build_output(data: dict) -> ScoredOutput:
    industry_fit = data.get("industry_fit", 0)
    company_size_fit = data.get("company_size_fit", 0)
    decision_maker_seniority = data.get("decision_maker_seniority", 0)
    budget_likelihood_score = data.get("budget_likelihood_score", 0)
    growth_signals = data.get("growth_signals", 0)

    lead_score = min(100, industry_fit + company_size_fit + decision_maker_seniority + budget_likelihood_score + growth_signals)

    raw_likelihood = data.get("budget_likelihood", "")
    if raw_likelihood in ("high", "medium", "low"):
        budget_likelihood = raw_likelihood
    elif budget_likelihood_score >= 15:
        budget_likelihood = "high"
    elif budget_likelihood_score >= 8:
        budget_likelihood = "medium"
    else:
        budget_likelihood = "low"

    return ScoredOutput(
        lead_score=lead_score,
        industry_fit=industry_fit,
        company_size_fit=company_size_fit,
        decision_maker_seniority=decision_maker_seniority,
        budget_likelihood_score=budget_likelihood_score,
        growth_signals=growth_signals,
        pain_points=data.get("pain_points", []),
        budget_likelihood=budget_likelihood,
        decision_maker=data.get("decision_maker", False),
        rationale=data.get("rationale", ""),
        signal_evidence=data.get("signal_evidence", {}),
    )


class ScorerClient:

    async def score(self, input: ScrapedInput) -> ScoredOutput:
        from anvil_scout.cli import run_once

        raw_out = run_once(json.dumps(input.to_dict()))
        data = json.loads(raw_out)

        if "error" in data:
            logger.error("Anvil Scout scoring error: %s", data["error"])
            raise ValueError(f"Scoring failed: {data['error']}")

        output = _build_output(data)
        logger.info(
            "Scoring complete: lead_score=%d industry_fit=%d company_size_fit=%d "
            "decision_maker_seniority=%d budget_likelihood_score=%d growth_signals=%d",
            output.lead_score,
            output.industry_fit,
            output.company_size_fit,
            output.decision_maker_seniority,
            output.budget_likelihood_score,
            output.growth_signals,
        )
        return output

    async def ingest_outcome(
        self,
        lead_id: str,
        score_at_emission: int,
        confidence_at_emission: float,
        label: str,
    ) -> None:
        from anvil_scout.daedalus.outcomes import (
            NullOutcomeProvider,
            OutcomeLabel,
            make_outcome,
        )

        outcome_label = OutcomeLabel(label)
        outcome = make_outcome(
            lead_id=lead_id,
            label=outcome_label,
            score_at_emission=score_at_emission,
            confidence_at_emission=confidence_at_emission,
        )
        provider = NullOutcomeProvider()
        provider.submit(outcome)
        logger.info(
            "Outcome submitted: lead_id=%s label=%s score=%s confidence=%s",
            lead_id,
            label,
            score_at_emission,
            confidence_at_emission,
        )
