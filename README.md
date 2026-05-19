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

```
HubSpot Contact Created
        |
        v
HubSpot Webhook  ---->  FastAPI (/webhook)
                             |
                             v
                      Celery Task Queue (Redis)
                             |
          +------------------+------------------+
          |                  |                  |
          v                  v                  v
      Firecrawl         Anvil Scout        Claude (Haiku)
    (website scrape)  (deterministic      (email draft)
                        scoring)
          |                  |                  |
          +------------------+------------------+
                             |
                             v
                      Postgres (Railway)
                             |
                    +--------+--------+
                    |                 |
                    v                 v
            HubSpot API        UI Extension Panel
           (write scores)      (contact record view)
```

### Key Modules

| Module | Purpose |
|--------|---------|
| `main.py` | FastAPI app, lifespan, router registration |
| `config.py` | Pydantic-settings — env vars + validation |
| `db/` | Migrations (`migrations.py`) and repository layer |
| `db/migrations.py` | `run_migrations()` — bootstraps 4 tables on startup |
| `db/repository.py` | Data access layer |
| `routers/v1/` | API route handlers (health, webhooks, panels) |
| `clients/` | External API wrappers (HubSpot, Firecrawl, Anvil Scout) |
| `workers/` | Celery tasks (scoring pipeline) |
| `engine/` | Anvil Scout deterministic scoring engine |
| `prompts/` | Claude prompt templates |
| `models.py` | Pydantic schemas |

### Database Schema

| Table | Purpose |
|-------|---------|
| `scored_leads` | Full lead profile, AI scores, draft email, outcomes |
| `outcome_events` | Status transitions (e.g., `pending` -> `contacted`) |
| `scoring_jobs` | Celery job tracking per contact |
| `hubspot_connections` | OAuth tokens per portal (multi-tenant) |

**Index:** `idx_sherlock_signal_hlip` — GIN on `sherlock_signal` (JSONB) for fast filtering.

## Local Development

### 1. Clone & Install

```bash
git clone https://github.com/niallb01/anvil-hlip-api.git
cd anvil-hlip-api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your keys:

```ini
ANTHROPIC_API_KEY=sk-ant-...
FIRECRAWL_API_KEY=fc-...
DATABASE_URL=postgresql://user:pass@localhost:5432/anvil
REDIS_URL=redis://localhost:6379/0
HUBSPOT_CLIENT_ID=...
HUBSPOT_CLIENT_SECRET=...
HUBSPOT_APP_ID=...
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
RESEND_API_KEY=re_...
ANVIL_API_KEY=...
RENDER_URL=https://your-app.onrender.com
```

### 3. Run

```bash
# Postgres & Redis must be running locally
uvicorn main:app --reload
```

Visit: `http://localhost:8000/api/v1/health`

## Deployment (Render)

1. Push to GitHub
2. Create new Web Service on [Render](https://render.com)
3. Point to this repo
4. Set environment variables (match `.env.example`)
5. `render.yaml` in repo root auto-configures build/start commands

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check |

*(Scoring, panel, and webhook endpoints coming next.)*

## Changelog

See [CHANGELOG.md](CHANGELOG.md)

## License

MIT
