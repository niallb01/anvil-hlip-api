import asyncio
import json
import logging

import asyncpg
import httpx
from celery import Celery

from clients.anthropic import AnthropicClient
from clients.firecrawl import FirecrawlClient
from clients.hubspot import HubSpotClient
from clients.scorer import ScrapedInput, ScorerClient
from clients.slack import SlackClient
from config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "anvil_hlip",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)


@celery_app.task(name="score_contact")
def score_contact(contact_id: str, portal_id: str) -> None:
    asyncio.run(_run_pipeline(contact_id, portal_id))


async def _run_pipeline(contact_id: str, portal_id: str) -> None:
    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
    except Exception:
        logger.exception("Pipeline DB connection failed for contact_id=%s", contact_id)
        return

    try:
        await _pipeline(contact_id, portal_id, conn)
    except Exception:
        logger.exception("Pipeline failed for contact_id=%s", contact_id)
        try:
            await conn.execute(
                "UPDATE scoring_jobs SET status = 'failed' WHERE contact_id = $1 AND status = 'running'",
                contact_id,
            )
        except Exception:
            logger.exception("Failed to mark scoring_job failed for contact_id=%s", contact_id)
    finally:
        await conn.close()


async def _pipeline(contact_id: str, portal_id: str, conn) -> None:
    # a. Mark job as running
    await conn.execute(
        "UPDATE scoring_jobs SET status = 'running' WHERE contact_id = $1 AND status = 'queued'",
        contact_id,
    )

    # b. Get HubSpot access token
    hs = HubSpotClient()
    access_token = await hs.get_access_token(portal_id, conn)
    if not access_token:
        raise RuntimeError(f"No HubSpot access token for portal_id={portal_id}")

    # c. Fetch contact properties from HubSpot
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"properties": "firstname,lastname,jobtitle,company,website"},
        )
        response.raise_for_status()
        contact_data = response.json()

    props = contact_data.get("properties", {})
    first_name = props.get("firstname") or ""
    last_name = props.get("lastname") or ""
    job_title = props.get("jobtitle") or ""
    company = props.get("company") or ""
    website_url = props.get("website") or ""
    full_name = f"{first_name} {last_name}".strip()

    logger.info("Contact fetched: contact_id=%s company=%s website=%s", contact_id, company, website_url)

    # d. Scrape website
    firecrawl = FirecrawlClient()
    scrape_result = (
        await firecrawl.scrape(website_url)
        if website_url
        else {"content": "", "url": website_url, "thin": True}
    )
    website_content = scrape_result["content"]
    scrape_quality = "thin" if scrape_result["thin"] else "good"

    # e. Score
    scorer = ScorerClient()
    scored = await scorer.score(ScrapedInput(
        name=full_name,
        website_url=website_url,
        website_content=website_content,
        title=job_title,
        company=company,
    ))
    signal_evidence = scored.signal_evidence if isinstance(scored.signal_evidence, dict) else {}
    confidence = signal_evidence.get("confidence", 0.0)

    # f. Generate outreach
    anthropic = AnthropicClient()
    outreach = await anthropic.generate_outreach(
        first_name=first_name,
        job_title=job_title,
        company=company,
        website_content=website_content,
        scrape_quality=scrape_quality,
        pain_points=scored.pain_points,
        rationale=scored.rationale,
    )

    # g. Build sherlock_signal
    sherlock_signal = {
        "schema_version": "v1",
        "product": "hlip",
        "confidence": confidence,
        "signals": {
            "lead_score": scored.lead_score,
            "industry_fit": scored.industry_fit,
            "company_size_fit": scored.company_size_fit,
            "decision_maker_seniority": scored.decision_maker_seniority,
            "budget_likelihood_score": scored.budget_likelihood_score,
            "growth_signals": scored.growth_signals,
            "decision_maker": scored.decision_maker,
            "budget_likelihood": scored.budget_likelihood,
            "thin_scrape": signal_evidence.get("thin_scrape", False),
        },
    }

    # h. Persist to scored_leads
    await conn.execute(
        """
        INSERT INTO scored_leads (
            contact_id, portal_id, first_name, last_name, job_title, company, website_url,
            lead_score_ai, industry_fit_ai, company_size_fit_ai, decision_maker_seniority_ai,
            budget_likelihood_score_ai, growth_signals_ai, pain_points_ai,
            budget_likelihood_ai, decision_maker_ai, rationale_ai,
            signal_evidence, scrape_quality, confidence_at_emission,
            draft_subject, draft_body, draft_created_at,
            sherlock_signal
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7,
            $8, $9, $10, $11,
            $12, $13, $14,
            $15, $16, $17,
            $18, $19, $20,
            $21, $22, NOW(),
            $23
        )
        ON CONFLICT (contact_id) DO UPDATE SET
            lead_score_ai = EXCLUDED.lead_score_ai,
            industry_fit_ai = EXCLUDED.industry_fit_ai,
            company_size_fit_ai = EXCLUDED.company_size_fit_ai,
            decision_maker_seniority_ai = EXCLUDED.decision_maker_seniority_ai,
            budget_likelihood_score_ai = EXCLUDED.budget_likelihood_score_ai,
            growth_signals_ai = EXCLUDED.growth_signals_ai,
            pain_points_ai = EXCLUDED.pain_points_ai,
            budget_likelihood_ai = EXCLUDED.budget_likelihood_ai,
            decision_maker_ai = EXCLUDED.decision_maker_ai,
            rationale_ai = EXCLUDED.rationale_ai,
            signal_evidence = EXCLUDED.signal_evidence,
            scrape_quality = EXCLUDED.scrape_quality,
            confidence_at_emission = EXCLUDED.confidence_at_emission,
            draft_subject = EXCLUDED.draft_subject,
            draft_body = EXCLUDED.draft_body,
            draft_created_at = EXCLUDED.draft_created_at,
            sherlock_signal = EXCLUDED.sherlock_signal,
            scored_at = NOW()
        """,
        str(contact_id),
        str(portal_id),
        first_name,
        last_name,
        job_title,
        company,
        website_url,
        scored.lead_score,
        scored.industry_fit,
        scored.company_size_fit,
        scored.decision_maker_seniority,
        scored.budget_likelihood_score,
        scored.growth_signals,
        ", ".join(scored.pain_points) if scored.pain_points else "",
        scored.budget_likelihood,
        scored.decision_maker,
        scored.rationale,
        json.dumps(signal_evidence),
        scrape_quality,
        confidence,
        outreach["subject"],
        outreach["body"],
        json.dumps(sherlock_signal),
    )

    logger.info("Lead persisted: contact_id=%s lead_score=%s", contact_id, scored.lead_score)

    # i. Write HubSpot custom properties
    await hs.update_contact_properties(
        contact_id,
        access_token,
        {
            "lead_score_ai": str(scored.lead_score),
            "industry_fit_ai": str(scored.industry_fit),
            "company_size_fit_ai": str(scored.company_size_fit),
            "decision_maker_seniority_ai": str(scored.decision_maker_seniority),
            "budget_likelihood_score_ai": str(scored.budget_likelihood_score),
            "growth_signals_ai": str(scored.growth_signals),
            "pain_points_ai": ", ".join(scored.pain_points) if scored.pain_points else "",
            "budget_likelihood_ai": scored.budget_likelihood,
            "decision_maker_ai": "true" if scored.decision_maker else "false",
            "rationale_ai": scored.rationale,
            "anvil_outcome": "pending",
        },
    )

    # j. Write rationale note
    await hs.create_note(
        contact_id,
        access_token,
        f"Anvil HLIP Score: {scored.lead_score}/100\n\n{scored.rationale}",
    )

    # k. Write outreach draft note
    if outreach["subject"] or outreach["body"]:
        await hs.create_note(
            contact_id,
            access_token,
            f"Draft outreach\nSubject: {outreach['subject']}\n\n{outreach['body']}",
        )

    # l. Slack alert
    await SlackClient().send_alert(
        contact_id=contact_id,
        first_name=first_name,
        last_name=last_name,
        company=company,
        job_title=job_title,
        lead_score=scored.lead_score,
        budget_likelihood=scored.budget_likelihood,
        decision_maker=scored.decision_maker,
    )

    # m. Mark job completed
    await conn.execute(
        "UPDATE scoring_jobs SET status = 'completed', completed_at = NOW() WHERE contact_id = $1",
        contact_id,
    )

    logger.info("Pipeline complete: contact_id=%s", contact_id)
