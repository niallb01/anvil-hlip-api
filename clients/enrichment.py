import logging
from urllib.parse import urlparse

import httpx

from config import settings

logger = logging.getLogger(__name__)


def _extract_domain(url: str) -> str:
    """Extract clean domain from a URL."""
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    return domain.replace("www.", "").strip()


class ApolloEnrichmentClient:

    async def enrich_organisation(self, website_url: str) -> dict:
        domain = _extract_domain(website_url)
        if not domain:
            logger.warning("No domain extracted from url=%s", website_url)
            return {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.apollo.io/api/v1/organizations/enrich",
                    headers={
                        "accept": "application/json",
                        "Cache-Control": "no-cache",
                        "Content-Type": "application/json",
                        "x-api-key": settings.APOLLO_API_KEY,
                    },
                    params={"domain": domain},
                )
                response.raise_for_status()
                data = response.json()
                org = data.get("organization") or {}
                logger.info(
                    "Apollo org enrichment: domain=%s employees=%s industry=%s",
                    domain,
                    org.get("estimated_num_employees"),
                    org.get("industry"),
                )
                return org
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Apollo org enrichment failed: domain=%s status=%s body=%s",
                domain, exc.response.status_code, exc.response.text,
            )
            return {}
        except httpx.TimeoutException:
            logger.error("Apollo org enrichment timeout: domain=%s", domain)
            return {}
        except Exception:
            logger.exception("Apollo org enrichment error: domain=%s", domain)
            return {}

    async def enrich_person(
        self,
        first_name: str,
        last_name: str,
        email: str,
        website_url: str,
    ) -> dict:
        domain = _extract_domain(website_url)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.apollo.io/api/v1/people/match",
                    headers={
                        "accept": "application/json",
                        "Cache-Control": "no-cache",
                        "Content-Type": "application/json",
                        "x-api-key": settings.APOLLO_API_KEY,
                    },
                    json={
                        "first_name": first_name,
                        "last_name": last_name,
                        "email": email,
                        "domain": domain,
                    },
                )
                response.raise_for_status()
                data = response.json()
                person = data.get("person") or {}
                logger.info(
                    "Apollo person enrichment: name=%s %s seniority=%s",
                    first_name, last_name,
                    person.get("seniority"),
                )
                return person
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Apollo person enrichment failed: email=%s status=%s body=%s",
                email, exc.response.status_code, exc.response.text,
            )
            return {}
        except httpx.TimeoutException:
            logger.error("Apollo person enrichment timeout: email=%s", email)
            return {}
        except Exception:
            logger.exception("Apollo person enrichment error: email=%s", email)
            return {}


def build_enrichment_result(org: dict, person: dict) -> dict:
    """Build a clean enrichment result from Apollo org and person data."""
    employee_count = org.get("estimated_num_employees")
    funding_stage = None
    latest_funding = org.get("latest_funding_stage")
    if latest_funding:
        stage_map = {
            "seed": "seed",
            "series_a": "seriesA",
            "series_b": "seriesB",
            "series_c": "seriesC+",
            "series_d": "seriesC+",
            "series_e": "seriesC+",
            "ipo": "ipo",
            "bootstrapped": "bootstrapped",
        }
        funding_stage = stage_map.get(latest_funding.lower(), None)

    industry = org.get("industry") or org.get("keywords", [None])[0]

    seniority = person.get("seniority", "")
    decision_maker_confirmed = seniority in (
        "c_suite", "vp", "director", "head", "owner", "founder", "partner"
    )

    tech_stack = [
        t.get("name", "") for t in (org.get("technology_names") or [])
    ]

    return {
        "available": bool(org),
        "employee_count": employee_count,
        "funding_stage": funding_stage,
        "industry_class": industry,
        "decision_maker_confirmed": decision_maker_confirmed,
        "seniority": seniority,
        "tech_stack": tech_stack,
    }