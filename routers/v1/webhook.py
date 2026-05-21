import json
import logging

import asyncpg
import httpx
from fastapi import APIRouter, HTTPException, Request

from clients.hubspot import HubSpotClient
from config import settings
from workers.scoring_pipeline import score_contact

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhook/contact", status_code=202)
async def contact_webhook(request: Request):
    raw_body = await request.body()
    logger.info("Webhook received — raw body: %s", raw_body[:500])

    try:
        body = json.loads(raw_body)
        payload = body[0] if isinstance(body, list) else body
    except Exception:
        raise HTTPException(status_code=400, detail={"message": "Invalid JSON body"})

    logger.info("Webhook payload: %s", json.dumps(payload))

    subscription_type = payload.get("subscriptionType")
    contact_id = str(payload.get("objectId", ""))
    portal_id = str(payload.get("portalId", ""))

    if subscription_type != "object.creation":
        logger.info("Ignoring subscriptionType=%s", subscription_type)
        return {"status": "ignored", "reason": f"subscriptionType={subscription_type}"}

    if not contact_id or not portal_id:
        raise HTTPException(status_code=422, detail={"message": "objectId and portalId are required"})

    logger.info("Processing object.creation: contact_id=%s portal_id=%s", contact_id, portal_id)

    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
    except Exception:
        logger.exception("DB connection failed")
        raise HTTPException(status_code=503, detail={"message": "Database unavailable"})

    try:
        existing = await conn.fetchrow(
            "SELECT id FROM scored_leads WHERE contact_id = $1",
            contact_id,
        )
        if existing:
            logger.info("Contact already scored, skipping: contact_id=%s", contact_id)
            return {"status": "already_scored", "contact_id": contact_id}

        hs = HubSpotClient()
        access_token = await hs.get_access_token(portal_id, conn)
        first_name = last_name = job_title = company = website_url = email = ""

        if access_token:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
                        headers={"Authorization": f"Bearer {access_token}"},
                        params={"properties": "firstname,lastname,jobtitle,company,website,email"},
                    )
                    response.raise_for_status()
                    props = response.json().get("properties", {})
                    first_name = props.get("firstname") or ""
                    last_name = props.get("lastname") or ""
                    job_title = props.get("jobtitle") or ""
                    company = props.get("company") or ""
                    website_url = props.get("website") or ""
                    email = props.get("email") or ""
                    logger.info("Contact properties fetched: contact_id=%s company=%s", contact_id, company)
            except Exception:
                logger.exception("Failed to fetch contact properties for contact_id=%s", contact_id)
        else:
            logger.warning("No access token for portal_id=%s — enqueuing with empty properties", portal_id)

        await conn.execute(
            """
            INSERT INTO scoring_jobs (contact_id, portal_id, status)
            VALUES ($1, $2, 'queued')
            """,
            contact_id,
            portal_id,
        )

        score_contact.delay(
            contact_id, portal_id,
            first_name, last_name, job_title, company, website_url, email,
        )

        logger.info("Enqueued scoring job: contact_id=%s", contact_id)

    except Exception:
        logger.exception("Error processing webhook for contact_id=%s", contact_id)
        raise HTTPException(status_code=500, detail={"message": "Internal server error"})
    finally:
        await conn.close()

    return {"status": "queued", "contact_id": contact_id}