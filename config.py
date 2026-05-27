from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ANTHROPIC_API_KEY: str = ""
    FIRECRAWL_API_KEY: str = ""
    DATABASE_URL: str = ""
    REDIS_URL: str = ""
    HUBSPOT_CLIENT_ID: str = ""
    HUBSPOT_CLIENT_SECRET: str = ""
    HUBSPOT_APP_ID: str = ""
    SLACK_WEBHOOK_URL: str = ""
    RESEND_API_KEY: str = ""
    ANVIL_API_KEY: str = ""
    RENDER_URL: str = ""
    APOLLO_API_KEY: str = ""

    model_config = {"env_file": ".env"}


settings = Settings()
