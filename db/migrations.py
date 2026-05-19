import asyncpg
from config import settings


async def run_migrations() -> None:
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS scored_leads (
                    id SERIAL PRIMARY KEY,
                    contact_id VARCHAR(255) UNIQUE,
                    portal_id VARCHAR(255),
                    email VARCHAR(255),
                    first_name VARCHAR(255),
                    last_name VARCHAR(255),
                    job_title VARCHAR(255),
                    company VARCHAR(255),
                    website_url VARCHAR(500),
                    lead_score_ai INTEGER,
                    industry_fit_ai INTEGER,
                    company_size_fit_ai INTEGER,
                    decision_maker_seniority_ai INTEGER,
                    budget_likelihood_score_ai INTEGER,
                    growth_signals_ai INTEGER,
                    pain_points_ai TEXT,
                    budget_likelihood_ai VARCHAR(50),
                    decision_maker_ai BOOLEAN,
                    rationale_ai TEXT,
                    signal_evidence JSONB,
                    scrape_quality VARCHAR(20),
                    confidence_at_emission FLOAT,
                    anvil_outcome VARCHAR(100) DEFAULT 'pending',
                    deal_outcome_ai VARCHAR(50) DEFAULT 'pending',
                    draft_subject VARCHAR(500),
                    draft_body TEXT,
                    panel_hidden BOOLEAN DEFAULT false,
                    scored_at TIMESTAMP DEFAULT NOW(),
                    draft_created_at TIMESTAMP,
                    outcome_updated_at TIMESTAMP,
                    sherlock_signal JSONB
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS outcome_events (
                    id SERIAL PRIMARY KEY,
                    contact_id VARCHAR(255),
                    portal_id VARCHAR(255),
                    previous_status VARCHAR(50),
                    new_status VARCHAR(50),
                    daedalus_submitted BOOLEAN DEFAULT false,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS scoring_jobs (
                    id SERIAL PRIMARY KEY,
                    contact_id VARCHAR(255),
                    portal_id VARCHAR(255),
                    status VARCHAR(50) DEFAULT 'queued',
                    error TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    completed_at TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS hubspot_connections (
                    id SERIAL PRIMARY KEY,
                    portal_id VARCHAR(255) UNIQUE,
                    access_token TEXT,
                    refresh_token TEXT,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sherlock_signal_hlip
                ON scored_leads USING GIN (sherlock_signal)
            """)
    finally:
        await conn.close()
