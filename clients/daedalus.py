import json
import logging
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PostgresDaedalusState:
    """Async Postgres state provider for Daedalus. Per-portal state."""

    def __init__(self, conn, portal_id: str):
        self._conn = conn
        self._portal_id = portal_id

    async def load(self) -> dict:
        row = await self._conn.fetchrow(
            "SELECT state_json FROM daedalus_state WHERE portal_id = $1",
            self._portal_id,
        )
        if not row:
            return {}
        try:
            return dict(row["state_json"])
        except Exception:
            return {}

    async def save(self, state: dict) -> None:
        await self._conn.execute(
            """
            INSERT INTO daedalus_state (portal_id, state_json, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (portal_id) DO UPDATE SET
                state_json = $2,
                updated_at = NOW()
            """,
            self._portal_id,
            json.dumps(state),
        )


async def store_daedalus_episode(
    conn,
    portal_id: str,
    lead_id: str,
    scored_payload: dict,
    text_chars: int,
) -> None:
    """Store a scoring episode in Daedalus state after scoring."""
    try:
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
        from anvil_scout.daedalus.predictive import (
            remember_prediction_for_input,
            opaque_lead_id,
        )
        from anvil_scout.contracts import ScrapedInput

        state_provider = PostgresDaedalusState(conn, portal_id)
        state = await state_provider.load()

        from anvil_scout.daedalus.predictive import (
            predict_from_state,
            store_episode,
            input_fingerprint,
            ensure_prediction_state,
        )

        prediction = predict_from_state(scored_payload, state)
        fp = "fp_" + lead_id[5:] if lead_id.startswith("lead_") else lead_id

        store_episode(
            state,
            lead_id=lead_id,
            input_fp=fp,
            payload=scored_payload,
            prediction=prediction,
            text_chars=text_chars,
            enrichment_available=False,
        )

        await state_provider.save(state)
        logger.info("Daedalus episode stored: lead_id=%s portal_id=%s", lead_id, portal_id)

    except Exception:
        logger.exception("Daedalus episode storage failed — pipeline continues")


async def submit_daedalus_outcome(
    conn,
    portal_id: str,
    lead_id: str,
    label: str,
) -> None:
    """Submit an outcome to Daedalus and update state."""
    try:
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
        from anvil_scout.daedalus.predictive import apply_outcome_to_state
        from anvil_scout.daedalus.outcomes import OutcomeLabel

        outcome_map = {
            "won": OutcomeLabel.WON,
            "lost": OutcomeLabel.LOST,
            "nurture": OutcomeLabel.NURTURE,
        }

        label_enum = outcome_map.get(label.lower())
        if not label_enum:
            logger.warning("Daedalus: unknown outcome label %s", label)
            return

        state_provider = PostgresDaedalusState(conn, portal_id)
        state = await state_provider.load()

        receipt = apply_outcome_to_state(
            state,
            lead_id=lead_id,
            label=label_enum,
        )

        await state_provider.save(state)
        logger.info(
            "Daedalus outcome submitted: lead_id=%s label=%s update_applied=%s",
            lead_id, label, receipt.get("update_applied"),
        )

    except Exception:
        logger.exception("Daedalus outcome submission failed — pipeline continues")