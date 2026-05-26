import asyncio
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

    async def scrape_lead(self, base_url: str) -> dict:
        base = base_url.rstrip("/")
        urls = {
            "home": base,
            "about": base + "/about",
            "pricing": base + "/pricing",
            "team": base + "/team",
            "careers": base + "/careers",
        }
        limits = {"home": 1500, "about": 1200, "pricing": 1300, "team": 1000, "careers": 1000}

        results = await asyncio.gather(
            self.scrape(urls["home"]),
            self.scrape(urls["about"]),
            self.scrape(urls["pricing"]),
            self.scrape(urls["team"]),
            self.scrape(urls["careers"]),
        )

        pages = {}
        sections = []
        for (key, url), result in zip(urls.items(), results):
            truncated = result["content"][:limits[key]]
            pages[key] = {"url": url, "thin": result["thin"]}
            if len(truncated) >= 100:
                sections.append(truncated)

        combined = "\n\n---\n\n".join(sections)
        all_thin = all(p["thin"] for p in pages.values())

        logger.info(
            "scrape_lead complete: base=%s pages=%d combined_chars=%d all_thin=%s",
            base, len(sections), len(combined), all_thin,
        )

        return {
            "content": combined,
            "url": base,
            "thin": all_thin,
            "pages": pages,
        }
