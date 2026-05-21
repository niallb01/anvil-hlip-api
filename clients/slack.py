import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class SlackClient:

    async def send_alert(
        self,
        contact_id: str,
        first_name: str,
        last_name: str,
        company: str,
        job_title: str,
        lead_score: int,
        budget_likelihood: str,
        decision_maker: bool,
        website_url: str = "",
    ) -> None:
        if lead_score < 60:
            return

        dm = "Yes" if decision_maker else "No"
        text = (
            f"🎯 *New High-Score Lead*\n"
            f"*Name:* {first_name} {last_name}\n"
            f"*Company:* {company}\n"
            f"*Title:* {job_title}\n"
            f"*Score:* {lead_score}/100\n"
            f"*Budget:* {budget_likelihood.capitalize()}\n"
            f"*Decision maker:* {dm}\n"
            f"*HubSpot:* https://app.hubspot.com/contacts/{contact_id}"
        )

        if website_url:
            text += f"\n*Website:* {website_url}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    settings.SLACK_WEBHOOK_URL,
                    json={"text": text},
                )
                response.raise_for_status()
                logger.info("Slack alert sent for contact_id=%s score=%s", contact_id, lead_score)
        except Exception:
            logger.exception("Slack alert failed for contact_id=%s", contact_id)