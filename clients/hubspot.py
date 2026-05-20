import logging
from datetime import datetime, timedelta

import httpx

from config import settings

logger = logging.getLogger(__name__)


class HubSpotClient:

    async def update_contact_properties(
        self,
        contact_id: str,
        access_token: str,
        properties: dict,
    ) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.patch(
                    f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"properties": properties},
                )
                response.raise_for_status()
                logger.info("HubSpot contact updated: contact_id=%s", contact_id)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HubSpot update failed: contact_id=%s status=%s body=%s",
                contact_id, exc.response.status_code, exc.response.text,
            )
        except Exception as exc:
            logger.exception("HubSpot update error: contact_id=%s", contact_id)

    async def create_note(
        self,
        contact_id: str,
        access_token: str,
        body: str,
    ) -> None:
        timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
        payload = {
            "properties": {
                "hs_note_body": body,
                "hs_timestamp": timestamp_ms,
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                }
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.hubapi.com/crm/v3/objects/notes",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=payload,
                )
                response.raise_for_status()
                logger.info("HubSpot note created for contact_id=%s", contact_id)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HubSpot note failed: contact_id=%s status=%s body=%s",
                contact_id, exc.response.status_code, exc.response.text,
            )
        except Exception as exc:
            logger.exception("HubSpot note error: contact_id=%s", contact_id)

    async def get_access_token(
        self,
        portal_id: str,
        conn,
    ) -> str | None:
        row = await conn.fetchrow(
            "SELECT access_token, refresh_token, expires_at FROM hubspot_connections WHERE portal_id = $1",
            portal_id,
        )
        if not row:
            logger.warning("No HubSpot connection for portal_id=%s", portal_id)
            return None

        access_token = row["access_token"]
        refresh_token = row["refresh_token"]
        expires_at = row["expires_at"]

        if expires_at and expires_at > datetime.utcnow():
            return access_token

        logger.info("Refreshing HubSpot token for portal_id=%s", portal_id)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.hubspot.com/oauth/v1/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": settings.HUBSPOT_CLIENT_ID,
                        "client_secret": settings.HUBSPOT_CLIENT_SECRET,
                        "refresh_token": refresh_token,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HubSpot token refresh failed: portal_id=%s status=%s body=%s",
                portal_id, exc.response.status_code, exc.response.text,
            )
            return None
        except Exception as exc:
            logger.exception("HubSpot token refresh error: portal_id=%s", portal_id)
            return None

        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token", refresh_token)
        expires_in = data.get("expires_in", 1800)
        new_expires = datetime.utcnow() + timedelta(seconds=expires_in)

        await conn.execute(
            """
            UPDATE hubspot_connections
            SET access_token = $1, refresh_token = $2, expires_at = $3, updated_at = NOW()
            WHERE portal_id = $4
            """,
            new_access,
            new_refresh,
            new_expires,
            portal_id,
        )
        logger.info("HubSpot token refreshed for portal_id=%s", portal_id)
        return new_access
