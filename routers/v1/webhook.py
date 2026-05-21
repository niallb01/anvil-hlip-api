import hashlib
import hmac
import json
import logging

import asyncpg
import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from clients.hubspot import HubSpotClient
from config import settings
from workers.scoring_pipeline import score_contact

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_hubspot_signature(body_bytes: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(status_code=401, detail={"message": "Missing X-HubSpot-Signature header"})
    expected = hashlib.sha256(
        (settings.HUBSPOT_CLIENT_SECRET + body_bytes.decode("utf-8")).encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail={"message": "Invalid webhook signature"})


@router.post("/webhook/contact", status_code=200)
async def contact_webhook(
    request: Request,
    x_hubspot_signature: str | None = Header(default=None),
):
    raw_body = await request.body()
    logger.info("Webhook received — raw body: %s", raw_body[:500])

    _verify_hubspot_signature(raw_body, x_hubspot_signature)

    try:
        events = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail={"message": "Invalid JSON body"})

    logger.info("HubSpot webhook received: %d events — %s", len(events), json.dumps(events))

    creation_events = [e for e in events if e.get("subscriptionType") == "contact.creation"]

    if not creation_events:
        return {"status": "ignored", "reason": "no contact.creation events"}

    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
    except Exception:
        logger.exception("DB connection failed")
        raise HTTPException(status_code=503, detail={"message": "Database unavailable"})

    hs = HubSpotClient()
    results = []

    try:
        for event in creation_events:
            contact_id = str(event.get("objectId", ""))
            portal_id = str(event.get("portalId", ""))

            if not contact_id or not portal_id:
                logger.warning("Event missing objectId or portalId: %s", event)
                continue

            logger.info("Processing contact.creation: contact_id=%s portal_id=%s", contact_id, portal_id)

            existing = await conn.fetchrow(
                "SELECT id FROM scored_leads WHERE contact_id = $1",
                contact_id,
            )
            if existing:
                logger.info("Contact already scored, skipping: contact_id=%s", contact_id)
                results.append({"contact_id": contact_id, "status": "already_scored"})
                continue

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

            results.append({"contact_id": contact_id, "status": "queued"})
            logger.info("Enqueued scoring job: contact_id=%s", contact_id)

    except Exception:
        logger.exception("Error processing webhook events")
        raise HTTPException(status_code=500, detail={"message": "Internal server error"})
    finally:
        await conn.close()

    return {"status": "ok", "results": results}
