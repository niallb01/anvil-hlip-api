import logging
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Anvil HLIP Connected</title></head>
<body>
  <h2>Anvil HLIP is connected.</h2>
  <p>Your contacts will be scored automatically as they enter HubSpot.</p>
  <p>Open any contact record to see the Anvil panel.</p>
  <p>You can close this tab.</p>
</body>
</html>"""


def _error_html(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Anvil HLIP — Error</title></head>
<body>
  <h2>Something went wrong.</h2>
  <p>{message}</p>
</body>
</html>"""


@router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        logger.warning("OAuth callback received with no code")
        return HTMLResponse(content=_error_html("No authorisation code received from HubSpot."), status_code=400)

    logger.info("OAuth callback received — exchanging code for tokens")

    redirect_uri = settings.RENDER_URL + "/api/v1/oauth/callback"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.hubspot.com/oauth/v1/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.HUBSPOT_CLIENT_ID,
                    "client_secret": settings.HUBSPOT_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0,
            )
            response.raise_for_status()
            token_data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("HubSpot token exchange failed: %s %s", exc.response.status_code, exc.response.text)
        return HTMLResponse(
            content=_error_html(f"HubSpot token exchange failed: {exc.response.status_code}"),
            status_code=502,
        )
    except Exception as exc:
        logger.exception("Unexpected error during token exchange")
        return HTMLResponse(content=_error_html(f"Token exchange error: {exc}"), status_code=500)

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    hub_id = token_data.get("hub_id")

    if not access_token or not hub_id:
        logger.error("Token response missing required fields: %s", token_data)
        return HTMLResponse(content=_error_html("Incomplete token response from HubSpot."), status_code=502)

    portal_id = str(hub_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=1800)

    logger.info("Tokens received for portal_id=%s — upserting into DB", portal_id)

    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
    except Exception as exc:
        logger.exception("DB connection failed")
        return HTMLResponse(content=_error_html("Database unavailable."), status_code=503)

    try:
        await conn.execute(
            """
            INSERT INTO hubspot_connections (portal_id, access_token, refresh_token, expires_at, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (portal_id) DO UPDATE
                SET access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = NOW()
            """,
            portal_id,
            access_token,
            refresh_token,
            expires_at,
        )
    except Exception as exc:
        logger.exception("DB upsert failed for portal_id=%s", portal_id)
        return HTMLResponse(content=_error_html("Failed to store connection. Please try again."), status_code=500)
    finally:
        await conn.close()

    logger.info("OAuth flow complete for portal_id=%s", portal_id)
    return HTMLResponse(content=_SUCCESS_HTML, status_code=200)
