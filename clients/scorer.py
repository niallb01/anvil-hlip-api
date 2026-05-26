import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import List

from anthropic import AsyncAnthropic

from config import settings

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


SCORING_PROMPT = """You are a B2B lead intelligence analyst. Your job is to identify whether this contact is likely to benefit from an AI-powered lead qualification and sales research tool.

The product saves sales reps time by automatically researching leads, scoring them against an ICP, and generating personalised outreach. The ideal buyer is anyone who owns or feels the pain of manual sales research.

CONTACT DATA:
Name: {name}
Title: {title}
Company: {company}
Website: {website_url}

WEBSITE CONTENT:
{website_content}

═══════════════════════════════════════════
STEP 1 — SIGNAL EXTRACTION (do this first)
═══════════════════════════════════════════

Before scoring, extract what you can actually observe from the website content.

VERIFIED signals: directly observable from the website text. Specific and factual.
Examples: "pricing page with paid tiers visible", "careers page lists 4 open SDR roles", "case studies with named B2B clients", "mentions HubSpot or Salesforce integration"

WEAK signals: mentioned but unsubstantiated, or implied but not evidenced.
Examples: "claims to have enterprise clients but no names", "mentions sales team but no headcount", "references growth but no hiring data"

MISSING signals: absent from the scraped content but relevant to scoring.
Examples: "no evidence of a sales function", "no pricing page", "no team or careers page", "no CRM or outbound tooling mentioned"

HARD RULES for signal extraction:
- Only mark as VERIFIED if you can point to specific text evidence
- Absence of evidence is NOT a weak signal — it is a missing signal
- Do not infer intent or budget from thin content
- If the scrape is under 500 characters, set confidence to 0.2 and note thin_scrape: true
- Maximum 3 items in each array (verified, weak, missing)
- Each signal must be under 10 words — specific, not descriptive

═══════════════════════════════════════════
STEP 2 — ICP SCORING (use signals as evidence)
═══════════════════════════════════════════

Score using only what the signal extraction above uncovered.
Do not add new inferences at this stage.

SCORING RUBRIC (total 100 points):

role_fit (0-25):
Score based on how directly this person owns the sales research and lead qualification problem.
25 = VP Sales, Head of Sales, CRO, Chief Revenue Officer, Sales Director — owns the sales function and budget
20 = RevOps, Sales Operations, Head of Growth — directly manages sales efficiency and tooling
15 = SDR Manager, BDR Manager, Sales Enablement — feels the pain daily, likely influencer
10 = Account Executive, Senior SDR, BDR — experiences the problem but unlikely to own budget
5 = Marketing Manager, Demand Gen, Growth Manager — adjacent, may influence but rarely owns sales tooling budget
0 = C-suite non-revenue (CTO, CFO, CPO), HR, Finance, Engineering, or no title data

company_size_fit (0-20):
20 = Clear evidence of 20-200 employees — big enough to have a sales team, small enough to not have enterprise tooling
15 = Signals suggest 20-200 but not confirmed
8 = Appears smaller than 20 (likely no dedicated sales team yet) or larger than 200 (likely has existing tooling)
0 = Solo operator, micro-business, or enterprise 500+ (will have Salesforce, Outreach etc already)

industry_fit (0-20):
20 = B2B SaaS or technology company — selling to businesses, high likelihood of structured sales motion
15 = B2B professional services, agency, consulting, or recruitment — has sales reps doing manual research
10 = Mixed B2B/B2C or unclear model — may have relevant use case
5 = B2C with some B2B elements — low likelihood of fit
0 = Pure B2C, marketplace, non-commercial, or public sector

sales_motion_signals (0-20):
Evidence that this company has an active outbound or structured sales function.
20 = Multiple verified signals: hiring SDRs or BDRs, mentions CRM (HubSpot/Salesforce/Pipedrive), references outbound or prospecting
15 = One or two verified sales motion signals — active hiring for sales roles or CRM mentioned
10 = Weak sales signals — general mentions of sales or growth without specific evidence
5 = Minimal signals — small sales function implied but not evidenced
0 = No evidence of a sales function — purely inbound, self-serve, or no commercial signals

budget_likelihood_score (0-15):
15 = Pricing page with paid tiers, funded startup signals, or enterprise indicators — clearly spending on software
10 = Revenue-generating business with commercial intent — likely has software budget
5 = Early stage or unclear revenue model — budget uncertain
0 = Pre-revenue, nonprofit, or no commercial signals

═══════════════════════════════════════════
RESPONSE FORMAT
═══════════════════════════════════════════

Return ONLY valid JSON. No markdown, no backticks, no preamble.

{{
  "lead_score": 0-100,
  "industry_fit": 0-20,
  "company_size_fit": 0-20,
  "decision_maker_seniority": 0-25,
  "budget_likelihood_score": 0-15,
  "growth_signals": 0-20,
  "pain_points": ["string", "string", "string"],
  "budget_likelihood": "high | medium | low",
  "decision_maker": true | false,
  "rationale": "3 sentences exactly. Sentence 1: what the company does and who they sell to. Sentence 2: why this specific contact owns the sales research problem — reference their title and company stage. Sentence 3: the single strongest verified signal from the website that suggests they have the problem we solve — must be specific and observable, not inferred. Write for a sales rep about to make a call. Never reference the email or outreach. No technical jargon. No corporate language.",
  "signal_evidence": {{
    "verified": ["string"],
    "weak": ["string"],
    "missing": ["string"],
    "confidence": 0.0-1.0,
    "thin_scrape": false
  }}
}}

FIELD RULES:
- lead_score = industry_fit + company_size_fit + decision_maker_seniority + budget_likelihood_score + growth_signals
- decision_maker = true only if role_fit >= 20 (VP, Director, Head of, CRO, RevOps)
- budget_likelihood = "high" if budget_likelihood_score >= 10, "medium" if >= 5, "low" if below 5
- pain_points: 3 specific pain points this contact likely has around sales research and lead qualification. Written for a sales rep to reference in outreach. Specific to their role and company stage, not generic.

confidence scale:
0.9-1.0 = rich content across multiple pages, multiple verified signals
0.7-0.8 = adequate content, some verified signals
0.5-0.6 = thin content, mostly weak or missing signals
0.2-0.4 = very thin scrape, low confidence in any score"""


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
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        prompt = SCORING_PROMPT.format(
            name=input.name,
            title=input.title,
            company=input.company,
            website_url=input.website_url,
            website_content=input.website_content,
        )
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        clean = re.sub(r"```json\n?|```\n?", "", raw).strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error("Scoring JSON parse failed: %s | raw=%s", e, raw[:200])
            raise ValueError(f"Scoring returned invalid JSON: {e}")
        output = _build_output(data)
        logger.info(
            "Scoring complete: lead_score=%d (industry=%d size=%d role=%d budget=%d sales=%d)",
            output.lead_score,
            output.industry_fit,
            output.company_size_fit,
            output.decision_maker_seniority,
            output.budget_likelihood_score,
            output.growth_signals,
        )
        return output
