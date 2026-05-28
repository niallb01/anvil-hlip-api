import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
from anvil_scout.cli import run_once

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
    predicted_quality: float = 0.5


def _build_output(data: dict, enrichment: dict | None = None) -> ScoredOutput:
    industry_fit = data.get("industry_fit", 0)
    company_size_fit = data.get("company_size_fit", 0)
    decision_maker_seniority = data.get("decision_maker_seniority", 0)
    budget_likelihood_score = data.get("budget_likelihood_score", 0)
    growth_signals = data.get("growth_signals", 0)

    # Override company_size_fit with Apollo employee count if available
    if enrichment and enrichment.get("available") and enrichment.get("employee_count"):
        emp = enrichment["employee_count"]
        if 20 <= emp <= 200:
            company_size_fit = 25
        elif 201 <= emp <= 500:
            company_size_fit = 15
        elif emp < 20:
            company_size_fit = 8
        else:
            company_size_fit = 8

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
        pain_points=[],
        budget_likelihood=budget_likelihood,
        decision_maker=data.get("decision_maker", False),
        rationale="",
        signal_evidence=data.get("signal_evidence", {}),
        predicted_quality=float(data.get("predicted_quality", 0.5)),
    )


class ScorerClient:

    async def score(self, input: ScrapedInput, enrichment: dict | None = None) -> ScoredOutput:
        from anvil_scout.core.enrichment import EnrichmentResult, set_provider

        if enrichment and enrichment.get("available"):
            class _ApolloProvider:
                def fetch(self, company: str, website_url: str) -> EnrichmentResult:
                    return EnrichmentResult(
                        available=True,
                        employee_count=enrichment.get("employee_count"),
                        funding_stage=enrichment.get("funding_stage"),
                        industry_class=enrichment.get("industry_class"),
                        decision_maker_confirmed=enrichment.get("decision_maker_confirmed"),
                    )
            set_provider(_ApolloProvider())
            logger.info("Scout enrichment provider set: employees=%s industry=%s",
                enrichment.get("employee_count"), enrichment.get("industry_class"))

        self._current_enrichment = enrichment

        raw_in = json.dumps({
            "name": input.name,
            "title": input.title,
            "company": input.company,
            "website_url": input.website_url,
            "website_content": input.website_content,
        })

        loop = asyncio.get_event_loop()
        raw_out = await loop.run_in_executor(None, run_once, raw_in)

        try:
            data = json.loads(raw_out)
        except json.JSONDecodeError as e:
            logger.error("Engine JSON parse failed: %s | raw=%s", e, raw_out[:200])
            raise ValueError(f"Engine returned invalid JSON: {e}")

        if "error" in data:
            logger.error("Engine returned error: %s", data["error"])
            raise ValueError(f"Engine error: {data['error']}")

        output = _build_output(data, enrichment=self._current_enrichment)
        logger.info(
            "Engine scoring complete: lead_score=%d (industry=%d size=%d role=%d budget=%d growth=%d)",
            output.lead_score,
            output.industry_fit,
            output.company_size_fit,
            output.decision_maker_seniority,
            output.budget_likelihood_score,
            output.growth_signals,
        )
        return output
