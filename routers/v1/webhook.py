import logging

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from config import settings
from workers.scoring_pipeline import score_contact

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_api_key(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"message": "Missing or malformed Authorization header"})
    token = authorization.removeprefix("Bearer ")
    if token != settings.ANVIL_API_KEY:
        raise HTTPException(status_code=403, detail={"message": "Invalid API key"})


@router.post("/webhook/contact", status_code=202)
async def contact_webhook(request: Request, authorization: str | None = Header(default=None)):
    _verify_api_key(authorization)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"message": "Invalid JSON body"})

    contact_id = body.get("contact_id")
    portal_id = body.get("portal_id")
    email = body.get("email")
    first_name = body.get("first_name")
    last_name = body.get("last_name")
    job_title = body.get("job_title")
    company = body.get("company")
    website_url = body.get("website_url")

    if not contact_id or not portal_id:
        raise HTTPException(status_code=422, detail={"message": "contact_id and portal_id are required"})

    logger.info("Incoming webhook: contact_id=%s portal_id=%s email=%s", contact_id, portal_id, email)

    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
    except Exception as exc:
        logger.exception("DB connection failed")
        raise HTTPException(status_code=503, detail={"message": "Database unavailable"}) from exc

    try:
        existing = await conn.fetchrow(
            "SELECT id FROM scored_leads WHERE contact_id = $1",
            str(contact_id),
        )
        if existing:
            return JSONResponse(status_code=200, content={"status": "already_scored"})

        await conn.execute(
            """
            INSERT INTO scoring_jobs (contact_id, portal_id, status)
            VALUES ($1, $2, 'queued')
            """,
            str(contact_id),
            str(portal_id),
        )
        score_contact.delay(
            str(contact_id),
            str(portal_id),
            first_name or "",
            last_name or "",
            job_title or "",
            company or "",
            website_url or "",
            email or "",
        )
    except Exception as exc:
        logger.exception("DB operation failed for contact_id=%s", contact_id)
        raise HTTPException(status_code=500, detail={"message": "Internal server error"}) from exc
    finally:
        await conn.close()

    return {"status": "queued", "contact_id": str(contact_id)}
