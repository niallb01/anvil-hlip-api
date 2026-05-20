import json
import logging
from pathlib import Path

import httpx

from config import settings

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "outreach_v1.md"


class AnthropicClient:

    async def generate_outreach(
        self,
        first_name: str,
        job_title: str,
        company: str,
        website_content: str,
        scrape_quality: str,
        pain_points: list[str],
        rationale: str,
    ) -> dict:
        try:
            template = _PROMPT_PATH.read_text(encoding="utf-8")
        except Exception as exc:
            logger.exception("Failed to load prompt template")
            return {"subject": "", "body": ""}

        prompt = template.format(
            first_name=first_name,
            job_title=job_title,
            company=company,
            website_content=website_content,
            scrape_quality=scrape_quality,
            pain_points=", ".join(pain_points) if pain_points else "none identified",
            rationale=rationale,
        )

        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1000,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Anthropic HTTP error: %s %s", exc.response.status_code, exc.response.text)
            return {"subject": "", "body": ""}
        except httpx.TimeoutException:
            logger.error("Anthropic timeout for company=%s", company)
            return {"subject": "", "body": ""}
        except Exception as exc:
            logger.exception("Anthropic unexpected error for company=%s", company)
            return {"subject": "", "body": ""}

        try:
            raw_text = data["content"][0]["text"]
            result = json.loads(raw_text)
            # return {"subject": result["subject"], "body": result["body"]}
            return {
    "subject": result["subject"],
    "body": result["body"],
    "followup_days": result.get("followup_days", 5)
}
        except Exception as exc:
            logger.error("Failed to parse Anthropic response: %s — raw: %s", exc, data)
            return {"subject": "", "body": ""}
