import logging
import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class IcpConfig(BaseModel):
    product_description: str = ""
    target_seniority: str = "VP, Director, Head of, CRO, Owner, MD"
    target_employee_min: int = 20
    target_employee_max: int = 200
    score_threshold: int = 40


async def _get_db_conn():
    try:
        return await asyncpg.connect(settings.DATABASE_URL)
    except Exception as exc:
        logger.exception("DB connection failed")
        raise HTTPException(status_code=503, detail={"message": "Database unavailable"}) from exc


@router.get("/portals/{portal_id}/icp")
async def get_icp_config(portal_id: str):
    conn = await _get_db_conn()
    try:
        row = await conn.fetchrow(
            "SELECT * FROM icp_configurations WHERE portal_id = $1",
            portal_id,
        )
        if not row:
            return IcpConfig().model_dump()
        return dict(row)
    finally:
        await conn.close()


@router.post("/portals/{portal_id}/icp")
async def save_icp_config(portal_id: str, config: IcpConfig):
    conn = await _get_db_conn()
    try:
        await conn.execute(
            """
            INSERT INTO icp_configurations (
                portal_id, product_description, target_seniority,
                target_employee_min, target_employee_max, score_threshold
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (portal_id) DO UPDATE SET
                product_description = $2,
                target_seniority = $3,
                target_employee_min = $4,
                target_employee_max = $5,
                score_threshold = $6,
                updated_at = NOW()
            """,
            portal_id,
            config.product_description,
            config.target_seniority,
            config.target_employee_min,
            config.target_employee_max,
            config.score_threshold,
        )
        logger.info("ICP config saved for portal_id=%s", portal_id)
        return {"status": "saved"}
    finally:
        await conn.close()
