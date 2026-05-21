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
        except Exception:
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
        except Exception:
            logger.exception("HubSpot note error: contact_id=%s", contact_id)

    async def create_email_draft(
        self,
        contact_id: str,
        access_token: str,
        subject: str,
        body: str,
    ) -> None:
        timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
        payload = {
            "properties": {
                "hs_email_subject": subject,
                "hs_email_html": f"<strong>Subject: {subject}</strong><br><br>" + body.replace("\n", "<br>"),
                "hs_email_direction": "EMAIL",
                "hs_email_status": "DRAFT",
                "hs_timestamp": timestamp_ms,
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 198}],
                }
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.hubapi.com/crm/v3/objects/emails",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=payload,
                )
                response.raise_for_status()
                logger.info("HubSpot email draft created for contact_id=%s", contact_id)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "HubSpot email draft failed: contact_id=%s status=%s body=%s",
                contact_id, exc.response.status_code, exc.response.text,
            )
        except Exception:
            logger.exception("HubSpot email draft error: contact_id=%s", contact_id)

    async def create_custom_properties(self, access_token: str) -> None:
        properties = [
            {"name": "lead_score_ai", "label": "Lead Score (Anvil)", "type": "number", "fieldType": "number"},
            {"name": "industry_fit_ai", "label": "Industry Fit (Anvil)", "type": "number", "fieldType": "number"},
            {"name": "company_size_fit_ai", "label": "Company Size Fit (Anvil)", "type": "number", "fieldType": "number"},
            {"name": "decision_maker_seniority_ai", "label": "Decision Maker Seniority (Anvil)", "type": "number", "fieldType": "number"},
            {"name": "budget_likelihood_score_ai", "label": "Budget Likelihood Score (Anvil)", "type": "number", "fieldType": "number"},
            {"name": "growth_signals_ai", "label": "Growth Signals (Anvil)", "type": "number", "fieldType": "number"},
            {"name": "pain_points_ai", "label": "Pain Points (Anvil)", "type": "string", "fieldType": "textarea"},
            {
                "name": "budget_likelihood_ai",
                "label": "Budget Likelihood (Anvil)",
                "type": "enumeration",
                "fieldType": "select",
                "options": [
                    {"label": "High", "value": "high"},
                    {"label": "Medium", "value": "medium"},
                    {"label": "Low", "value": "low"},
                    {"label": "Unknown", "value": "unknown"},
                ],
            },
            {
                "name": "decision_maker_ai",
                "label": "Decision Maker (Anvil)",
                "type": "bool",
                "fieldType": "booleancheckbox",
                "options": [
                    {"label": "Yes", "value": "true", "displayOrder": 0, "hidden": False},
                    {"label": "No", "value": "false", "displayOrder": 1, "hidden": False},
                ],
            },
            {"name": "rationale_ai", "label": "Rationale (Anvil)", "type": "string", "fieldType": "textarea"},
            {
                "name": "anvil_outcome",
                "label": "Anvil Outcome",
                "type": "enumeration",
                "fieldType": "select",
                "options": [
                    {"label": "Pending", "value": "pending"},
                    {"label": "In Progress", "value": "in_progress"},
                    {"label": "Nurture", "value": "nurture"},
                    {"label": "Won", "value": "won"},
                    {"label": "Lost", "value": "lost"},
                ],
            },
        ]
        async with httpx.AsyncClient(timeout=15.0) as client:
            for prop in properties:
                payload = {
                    "groupName": "contactinformation",
                    "name": prop["name"],
                    "label": prop["label"],
                    "type": prop["type"],
                    "fieldType": prop["fieldType"],
                }
                if "options" in prop:
                    payload["options"] = prop["options"]
                try:
                    response = await client.post(
                        "https://api.hubapi.com/crm/v3/properties/contacts",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json=payload,
                    )
                    if response.status_code == 409:
                        logger.info("HubSpot property already exists: %s", prop["name"])
                        continue
                    response.raise_for_status()
                    logger.info("HubSpot property created: %s", prop["name"])
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        "Failed to create property %s: %s %s",
                        prop["name"], exc.response.status_code, exc.response.text,
                    )
                except Exception:
                    logger.exception("Unexpected error creating property %s", prop["name"])

    async def create_sales_briefing_note(
        self,
        contact_id: str,
        access_token: str,
        first_name: str,
        last_name: str,
        job_title: str,
        company: str,
        lead_score: int,
        budget_likelihood: str,
        decision_maker: bool,
        confidence: float,
        draft_subject: str,
        draft_body: str,
        rationale: str,
    ) -> None:
        dm = "Yes" if decision_maker else "No"
        confidence_pct = round(confidence * 100, 1)

        briefing = (
    f"🎯 Anvil HLIP Briefing\n\n"
    f"Contact: {first_name} {last_name} | {job_title} at {company}\n\n"
    f"Score: {lead_score}/100\n"
    f"Budget likelihood: {budget_likelihood.capitalize()}\n"
    f"Decision maker: {dm}\n"
    f"Confidence: {confidence_pct}%"
        )

        await self.create_note(contact_id, access_token, briefing)
        await self.create_email_draft(contact_id, access_token, draft_subject, draft_body)

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
        except Exception:
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