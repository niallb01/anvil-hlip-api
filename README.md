# anvil-hlip-api

Backend API for Anvil HLIP — the lead scoring and outreach engine that runs inside HubSpot.

Every new HubSpot contact triggers a webhook. The system scrapes their website, scores them deterministically against an ICP using Anvil Scout, generates a Challenger Selling outreach email via Claude, writes scores back to HubSpot as custom properties, and serves everything to a UI Extension panel on the contact record.

## Stack
- **FastAPI** — API framework
- **Celery + Redis** — async job queue for scoring pipeline
- **asyncpg** — async Postgres driver
- **SQLAlchemy 2.x** — async ORM
- **Anvil Scout** — deterministic lead scorer (zero LLM)
- **Claude (Haiku)** — outreach email generation
- **Firecrawl** — website scraping
- **Postgres on Railway** — data layer
- **Deployed on Render**

## Architecture
