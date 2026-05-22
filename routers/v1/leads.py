import base64
import hashlib
import hmac
import logging
import time

import asyncpg
from fastapi import APIRouter, HTTPException, Request

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_OUTCOMES = {"pending", "in_progress", "nurture", "won", "lost"}
_MAX_TIMESTAMP_AGE_SECONDS = 300


async def _get_db_conn():
    try:
        return await asyncpg.connect(settings.DATABASE_URL)
    except Exception as exc:
        logger.exception("DB connection failed")
        raise HTTPException(status_code=503, detail={"message": "Database unavailable"}) from exc


async def _verify_hubspot_request(request: Request, conn) -> str:
    signature = request.headers.get("X-HubSpot-Signature-v3")
    timestamp = request.headers.get("X-HubSpot-Request-Timestamp")

    if not signature:
        raise HTTPException(status_code=401, detail={"message": "Missing X-HubSpot-Signature-v3 header"})
    if not timestamp:
        raise HTTPException(status_code=401, detail={"message": "Missing X-HubSpot-Request-Timestamp header"})

    try:
        ts_ms = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=403, detail={"message": "Invalid timestamp"})

    age_seconds = time.time() - ts_ms / 1000
    if age_seconds > _MAX_TIMESTAMP_AGE_SECONDS:
        raise HTTPException(status_code=403, detail={"message": "Request timestamp too old"})

    body_bytes = await request.body()
    source = request.method + str(request.url) + body_bytes.decode("utf-8") + timestamp

    expected = base64.b64encode(
        hmac.new(
            settings.HUBSPOT_CLIENT_SECRET.encode("utf-8"),
            source.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail={"message": "Invalid signature"})

    portal_id = request.query_params.get("portalId")
    if not portal_id:
        raise HTTPException(status_code=401, detail={"message": "Missing portalId query parameter"})

    row = await conn.fetchrow(
        "SELECT id FROM hubspot_connections WHERE portal_id = $1",
        portal_id,
    )
    if not row:
        raise HTTPException(status_code=403, detail={"message": "Portal not connected"})

    return portal_id


async def _get_lead_or_404(conn, contact_id: str):
    row = await conn.fetchrow(
        "SELECT * FROM scored_leads WHERE contact_id = $1",
        contact_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"message": "Lead not found"})
    return row


@router.get("/leads/{contact_id}")
async def get_lead(contact_id: str, request: Request):
    conn = await _get_db_conn()
    try:
        await _verify_hubspot_request(request, conn)
        row = await _get_lead_or_404(conn, contact_id)
        return dict(row)
    finally:
        await conn.close()


@router.post("/leads/{contact_id}/outcome")
async def update_outcome(contact_id: str, body: dict, request: Request):
    status = body.get("status")
    if status not in VALID_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Invalid status. Must be one of: {', '.join(sorted(VALID_OUTCOMES))}"},
        )

    conn = await _get_db_conn()
    try:
        await _verify_hubspot_request(request, conn)
        row = await _get_lead_or_404(conn, contact_id)
        previous = row["deal_outcome_ai"]

        await conn.execute(
            """
            UPDATE scored_leads
            SET deal_outcome_ai = $1, outcome_updated_at = NOW()
            WHERE contact_id = $2
            """,
            status,
            contact_id,
        )
        await conn.execute(
            """
            INSERT INTO outcome_events (contact_id, portal_id, previous_status, new_status, daedalus_submitted)
            VALUES ($1, $2, $3, $4, false)
            """,
            contact_id,
            row["portal_id"],
            previous,
            status,
        )
        logger.info("Outcome updated: contact_id=%s %s -> %s", contact_id, previous, status)
    finally:
        await conn.close()

    return {"status": "updated"}


@router.post("/leads/{contact_id}/hide")
async def hide_lead(contact_id: str, request: Request):
    conn = await _get_db_conn()
    try:
        await _verify_hubspot_request(request, conn)
        await _get_lead_or_404(conn, contact_id)
        await conn.execute(
            "UPDATE scored_leads SET panel_hidden = true WHERE contact_id = $1",
            contact_id,
        )
        logger.info("Lead hidden: contact_id=%s", contact_id)
    finally:
        await conn.close()

    return {"status": "hidden"}


@router.post("/leads/{contact_id}/show")
async def show_lead(contact_id: str, request: Request):
    conn = await _get_db_conn()
    try:
        await _verify_hubspot_request(request, conn)
        await _get_lead_or_404(conn, contact_id)
        await conn.execute(
            "UPDATE scored_leads SET panel_hidden = false WHERE contact_id = $1",
            contact_id,
        )
        logger.info("Lead shown: contact_id=%s", contact_id)
    finally:
        await conn.close()

    return {"status": "visible"}
