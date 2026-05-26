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
        last_name: str,
        job_title: str,
        company: str,
        website_url: str,
        website_content: str,
        scrape_quality: str,
        lead_score: int,
        decision_maker: bool,
        budget_likelihood: str,
        verified_signals: list[str],
        weak_signals: list[str],
        missing_signals: list[str],
    ) -> dict:
        try:
            template = _PROMPT_PATH.read_text(encoding="utf-8")
        except Exception:
            logger.exception("Failed to load prompt template")
            return {"subject": "", "body": "", "followup_days": 5, "rationale": "", "pain_points": []}

        name = f"{first_name} {last_name}".strip()
        prompt = template.format(
            name=name,
            first_name=first_name,
            job_title=job_title,
            company=company,
            website_url=website_url,
            website_content=website_content,
            scrape_quality=scrape_quality,
            lead_score=lead_score,
            decision_maker="Yes" if decision_maker else "No",
            budget_likelihood=budget_likelihood,
            verified_signals="\n".join(f"- {s}" for s in verified_signals) if verified_signals else "None identified",
            weak_signals="\n".join(f"- {s}" for s in weak_signals) if weak_signals else "None identified",
            missing_signals="\n".join(f"- {s}" for s in missing_signals) if missing_signals else "None identified",
        )

        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1500,
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
            return {"subject": "", "body": "", "followup_days": 5, "rationale": "", "pain_points": []}
        except httpx.TimeoutException:
            logger.error("Anthropic timeout for company=%s", company)
            return {"subject": "", "body": "", "followup_days": 5, "rationale": "", "pain_points": []}
        except Exception:
            logger.exception("Anthropic unexpected error for company=%s", company)
            return {"subject": "", "body": "", "followup_days": 5, "rationale": "", "pain_points": []}

        try:
            raw_text = data["content"][0]["text"].strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            result = json.loads(raw_text.strip())
            return {
                "subject": result["subject"],
                "body": result["body"],
                "followup_days": result.get("followup_days", 5),
                "rationale": result.get("rationale", ""),
                "pain_points": result.get("pain_points", []),
            }
        except Exception as exc:
            logger.error("Failed to parse Anthropic response: %s — raw: %s", exc, data)
            return {"subject": "", "body": "", "followup_days": 5, "rationale": "", "pain_points": []}