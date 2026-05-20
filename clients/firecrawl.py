import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class FirecrawlClient:

    async def scrape(self, url: str) -> dict:
        logger.info("Starting Firecrawl scrape: url=%s", url)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.firecrawl.dev/v1/scrape",
                    headers={"Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}"},
                    json={
                        "url": url,
                        "formats": ["markdown"],
                        "onlyMainContent": True,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Firecrawl HTTP error: %s %s", exc.response.status_code, exc.response.text)
            return {"content": "", "url": url, "thin": True}
        except httpx.TimeoutException:
            logger.error("Firecrawl timeout: url=%s", url)
            return {"content": "", "url": url, "thin": True}
        except Exception as exc:
            logger.exception("Firecrawl unexpected error: url=%s", url)
            return {"content": "", "url": url, "thin": True}

        markdown = data.get("data", {}).get("markdown", "") or ""
        thin = len(markdown) < 500
        logger.info("Firecrawl complete: url=%s chars=%d thin=%s", url, len(markdown), thin)

        return {
            "content": markdown,
            "url": url,
            "thin": thin,
        }
