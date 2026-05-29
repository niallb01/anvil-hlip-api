
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import asyncio
import json
import logging
import asyncpg
from celery import Celery

from anvil_pantheon.bridge_api import certify_lead
from clients.anthropic import AnthropicClient
from clients.daedalus import store_daedalus_episode
from clients.firecrawl import FirecrawlClient
from clients.hubspot import HubSpotClient
from clients.scorer import ScrapedInput, ScorerClient
from clients.slack import SlackClient
from config import settings
from routers.icp import get_icp_config

logger = logging.getLogger(__name__)

celery_app = Celery(
    "anvil_hlip",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)


@celery_app.task(name="score_contact")
def score_contact(
    contact_id: str,
    portal_id: str,
    first_name: str,
    last_name: str,
    job_title: str,
    company: str,
    website_url: str,
    email: str,
) -> None:
    asyncio.run(_run_pipeline(contact_id, portal_id, first_name, last_name, job_title, company, website_url, email))


async def _run_pipeline(
    contact_id: str,
    portal_id: str,
    first_name: str,
    last_name: str,
    job_title: str,
    company: str,
    website_url: str,
    email: str,
) -> None:
    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
    except Exception:
        logger.exception("Pipeline DB connection failed for contact_id=%s", contact_id)
        return

    try:
        await _pipeline(contact_id, portal_id, first_name, last_name, job_title, company, website_url, email, conn)
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


async def _pipeline(
    contact_id: str,
    portal_id: str,
    first_name: str,
    last_name: str,
    job_title: str,
    company: str,
    website_url: str,
    email: str,
    conn,
) -> None:
    full_name = f"{first_name} {last_name}".strip()

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

    # b2. Fetch ICP config for this portal
    icp = await get_icp_config(portal_id)

    # c. Scrape website — fallback to email domain if no website URL
    firecrawl = FirecrawlClient()
    effective_url = website_url
    if not effective_url and email and "@" in email:
        domain = email.split("@")[1]
        effective_url = f"https://{domain}"
        logger.info("No website URL — falling back to email domain: %s", effective_url)

    scrape_result = (
        await firecrawl.scrape_lead(effective_url)
        if effective_url
        else {"content": "", "url": "", "thin": True}
    )
    website_content = scrape_result["content"]
    scrape_quality = "thin" if scrape_result["thin"] else "good"

    # d. Enrich
    from clients.enrichment import ApolloEnrichmentClient, build_enrichment_result
    apollo = ApolloEnrichmentClient()
    org_data = await apollo.enrich_organisation(effective_url)
    enrichment = build_enrichment_result(org_data, {})
    logger.info(
        "Enrichment: available=%s employees=%s seniority=%s tech_stack=%s",
        enrichment["available"],
        enrichment["employee_count"],
        enrichment["seniority"],
        enrichment["tech_stack"][:3] if enrichment["tech_stack"] else [],
    )

    # e. Score
    scorer = ScorerClient()
    scored = await scorer.score(ScrapedInput(
        name=full_name,
        website_url=website_url,
        website_content=website_content,
        title=job_title,
        company=company,
    ), enrichment=enrichment, icp=icp)
    
    signal_evidence = scored.signal_evidence if isinstance(scored.signal_evidence, dict) else {}
    confidence = signal_evidence.get("signal_density", 0.0)

    # Generate opaque lead_id for Daedalus
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
    from anvil_scout.daedalus.predictive import opaque_lead_id
    from anvil_scout.contracts import ScrapedInput as _ScrapedInput
    daedalus_lead_id = opaque_lead_id(_ScrapedInput(
        name=full_name,
        title=job_title,
        company=company,
        website_url=effective_url,
        website_content=website_content,
    ))

    # Store Daedalus episode
    scored_payload = {
        "lead_score": scored.lead_score,
        "industry_fit": scored.industry_fit,
        "company_size_fit": scored.company_size_fit,
        "decision_maker_seniority": scored.decision_maker_seniority,
        "budget_likelihood_score": scored.budget_likelihood_score,
        "growth_signals": scored.growth_signals,
        "decision_maker": scored.decision_maker,
        "predicted_quality": scored.predicted_quality,
        "signal_evidence": signal_evidence,
    }
    await store_daedalus_episode(
        conn=conn,
        portal_id=portal_id,
        lead_id=daedalus_lead_id,
        scored_payload=scored_payload,
        text_chars=len(website_content),
    )

    # e. Generate outreach (skip if score too low)
    verified_signals = signal_evidence.get("verified", [])
    weak_signals = signal_evidence.get("weak", [])
    missing_signals = signal_evidence.get("missing", [])

    if scored.lead_score >= icp.get("score_threshold", 40):
        # Build scout output dict for Pantheon
        scout_output = {
            "lead_score": scored.lead_score,
            "industry_fit": scored.industry_fit,
            "company_size_fit": scored.company_size_fit,
            "decision_maker_seniority": scored.decision_maker_seniority,
            "budget_likelihood_score": scored.budget_likelihood_score,
            "growth_signals": scored.growth_signals,
            "pain_points": scored.pain_points,
            "budget_likelihood": scored.budget_likelihood,
            "decision_maker": scored.decision_maker,
            "predicted_quality": scored.predicted_quality,
            "rationale": scored.rationale,
            "signal_evidence": signal_evidence,
        }

        # Try Pantheon first — certified, no hallucination
        pantheon_result = certify_lead(scout_output)

        if not pantheon_result["refused"] and pantheon_result["rendered_text"]:
            # Pantheon succeeded — use certified output
            outreach = {
                "subject": f"Re: {company}",
                "body": pantheon_result["rendered_text"],
                "followup_days": 5 if scored.lead_score >= 80 else 7,
                "rationale": "",
                "pain_points": [],
            }
            logger.info("Pantheon certified email generated: contact_id=%s", contact_id)
        else:
            # Pantheon refused — no certified output available
            logger.info(
                "Pantheon refused (reasons=%s) — no outreach generated: contact_id=%s",
                pantheon_result.get("refusal_reasons", []),
                contact_id,
            )
            outreach = {"subject": "", "body": "", "followup_days": 0, "rationale": "", "pain_points": []}

        # Generate certified rationale via Pantheon rationale template
        rationale_result = certify_lead(
            scout_output,
            template_id="lead_rationale",
            template_version="v0.1",
        )
        if not rationale_result["refused"] and rationale_result["rendered_text"]:
            outreach["rationale"] = rationale_result["rendered_text"].strip()
            logger.info("Pantheon certified rationale generated: contact_id=%s", contact_id)
        else:
            logger.info(
                "Pantheon rationale refused (reasons=%s): contact_id=%s",
                rationale_result.get("refusal_reasons", []),
                contact_id,
            )
    else:
        outreach = {"subject": "", "body": "", "followup_days": 0, "rationale": "", "pain_points": []}
        logger.info("Outreach skipped: lead_score=%d below threshold for contact_id=%s", scored.lead_score, contact_id)

    # f. Build sherlock_signal
    sherlock_signal = {
        "schema_version": "v1",
        "product": "hlip",
        "signal_density": confidence,
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

    # g. Persist to scored_leads
    await conn.execute(
        """
        INSERT INTO scored_leads (
            contact_id, portal_id, email, first_name, last_name, job_title, company, website_url,
            lead_score_ai, industry_fit_ai, company_size_fit_ai, decision_maker_seniority_ai,
            budget_likelihood_score_ai, growth_signals_ai, pain_points_ai,
            budget_likelihood_ai, decision_maker_ai, rationale_ai,
            signal_evidence, scrape_quality, confidence_at_emission,
            draft_subject, draft_body, draft_created_at,
            sherlock_signal, predicted_quality, daedalus_lead_id
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8,
            $9, $10, $11, $12,
            $13, $14, $15,
            $16, $17, $18,
            $19, $20, $21,
            $22, $23, NOW(),
            $24, $25, $26
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
            predicted_quality = EXCLUDED.predicted_quality,
            daedalus_lead_id = EXCLUDED.daedalus_lead_id,
            scored_at = NOW()
        """,
        str(contact_id),
        str(portal_id),
        email,
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
        ", ".join(outreach.get("pain_points", [])),
        scored.budget_likelihood,
        scored.decision_maker,
        outreach.get("rationale", ""),
        json.dumps(signal_evidence),
        scrape_quality,
        confidence,
        outreach["subject"],
        outreach["body"],
        json.dumps(sherlock_signal),
        scored.predicted_quality,
        daedalus_lead_id,
    )

    logger.info("Lead persisted: contact_id=%s lead_score=%s", contact_id, scored.lead_score)

    # h. Write HubSpot custom properties
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
            "rationale_ai": outreach.get("rationale", ""),
            "predicted_quality_ai": str(round(scored.predicted_quality * 100)),
            "anvil_outcome": "pending",
        },
    )

    # i. Write sales briefing note
    await hs.create_sales_briefing_note(
        contact_id=contact_id,
        access_token=access_token,
        first_name=first_name,
        last_name=last_name,
        job_title=job_title,
        company=company,
        lead_score=scored.lead_score,
        budget_likelihood=scored.budget_likelihood,
        decision_maker=scored.decision_maker,
        confidence=confidence,
        draft_subject=outreach["subject"],
        draft_body=outreach["body"],
        rationale=scored.rationale,
    )

    # j. Slack alert
    await SlackClient().send_alert(
        contact_id=contact_id,
        first_name=first_name,
        last_name=last_name,
        company=company,
        job_title=job_title,
        lead_score=scored.lead_score,
        budget_likelihood=scored.budget_likelihood,
        decision_maker=scored.decision_maker,
        website_url=website_url,
    )

    # l. Mark job completed
    await conn.execute(
        "UPDATE scoring_jobs SET status = 'completed', completed_at = NOW() WHERE contact_id = $1",
        contact_id,
    )

    logger.info("Pipeline complete: contact_id=%s", contact_id)
