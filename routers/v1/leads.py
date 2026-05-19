import logging

import asyncpg
from fastapi import APIRouter, Header, HTTPException

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_OUTCOMES = {"pending", "in_progress", "nurture", "won", "lost"}


def _verify_api_key(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"message": "Missing or malformed Authorization header"})
    token = authorization.removeprefix("Bearer ")
    if token != settings.ANVIL_API_KEY:
        raise HTTPException(status_code=403, detail={"message": "Invalid API key"})


async def _get_db_conn():
    try:
        return await asyncpg.connect(settings.DATABASE_URL)
    except Exception as exc:
        logger.exception("DB connection failed")
        raise HTTPException(status_code=503, detail={"message": "Database unavailable"}) from exc


async def _get_lead_or_404(conn, contact_id: str):
    row = await conn.fetchrow(
        "SELECT * FROM scored_leads WHERE contact_id = $1",
        contact_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"message": "Lead not found"})
    return row


@router.get("/leads/{contact_id}")
async def get_lead(contact_id: str, authorization: str | None = Header(default=None)):
    _verify_api_key(authorization)
    conn = await _get_db_conn()
    try:
        row = await _get_lead_or_404(conn, contact_id)
        return dict(row)
    finally:
        await conn.close()


@router.post("/leads/{contact_id}/outcome")
async def update_outcome(
    contact_id: str,
    body: dict,
    authorization: str | None = Header(default=None),
):
    _verify_api_key(authorization)
    status = body.get("status")
    if status not in VALID_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Invalid status. Must be one of: {', '.join(sorted(VALID_OUTCOMES))}"},
        )

    conn = await _get_db_conn()
    try:
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
async def hide_lead(contact_id: str, authorization: str | None = Header(default=None)):
    _verify_api_key(authorization)
    conn = await _get_db_conn()
    try:
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
async def show_lead(contact_id: str, authorization: str | None = Header(default=None)):
    _verify_api_key(authorization)
    conn = await _get_db_conn()
    try:
        await _get_lead_or_404(conn, contact_id)
        await conn.execute(
            "UPDATE scored_leads SET panel_hidden = false WHERE contact_id = $1",
            contact_id,
        )
        logger.info("Lead shown: contact_id=%s", contact_id)
    finally:
        await conn.close()

    return {"status": "visible"}
