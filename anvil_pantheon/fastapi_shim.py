"""Anvil-Pantheon-Floor — FastAPI shim (Packet 13, OPTIONAL).

This module is an OPTIONAL convenience wrapper around bridge_api. It
provides a ready-to-import FastAPI APIRouter that Niall (or any
FastAPI consumer) can drop into their app:

    # In Niall's main.py:
    from anvil_pantheon.fastapi_shim import build_router
    from anvil_pantheon.services.mnemosyne import MnemosyneStore
    from anvil_pantheon.receipts import ReceiptStore

    store = MnemosyneStore(ReceiptStore("/var/anvil/receipts.jsonl"))
    app.include_router(build_router(store=store), prefix="/api/v1/pantheon")

FastAPI is imported LAZILY inside build_router(); the rest of this
module is import-safe even when FastAPI is not installed. This
preserves the bridge_api.py invariant that pantheon does not take
FastAPI as a hard dependency.

NON_CLAIMS:
  - Does NOT add auth (Niall's concern; he wraps the router)
  - Does NOT add rate limiting (same)
  - Does NOT persist requests on its own (only via the MnemosyneStore
    Niall passes in, if any)
  - Does NOT exist as a foundation module; it's a CONVENIENCE LAYER
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def build_router(
    store: Optional[Any] = None,
    *,
    prefix_within_router: str = "",
    tags: Optional[list] = None,
):
    """Build and return a FastAPI APIRouter exposing:

      POST {prefix_within_router}/certify
        body: {"scout_output": {...}, "template_id": "sales_email",
               "template_version": "v0.1"}
        returns: bridge_api.certify_lead(...) result

      GET  {prefix_within_router}/health
        returns: {"status": "ok", "modules_loaded": N}

    Arguments:
      store: optional MnemosyneStore for chain persistence; if None,
        certificates are not recorded (still returned in response).
      prefix_within_router: path prefix appended to each route within
        the returned router (Niall mounts with his own outer prefix).
      tags: FastAPI OpenAPI tags for the routes.

    Raises:
      ImportError: if FastAPI is not installed.
    """
    try:
        from fastapi import APIRouter, Body, HTTPException
    except ImportError as exc:
        raise ImportError(
            "fastapi_shim.build_router requires FastAPI. "
            "Install with: pip install fastapi. "
            "If you don't want FastAPI, call anvil_pantheon.bridge_api."
            "certify_lead() directly from your own orchestrator."
        ) from exc

    from .audit import FOUNDATION_COMPONENTS
    from .bridge_api import certify_lead

    router = APIRouter(tags=tags or ["pantheon"])

    @router.post(f"{prefix_within_router}/certify")
    async def certify(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        scout_output = payload.get("scout_output")
        if scout_output is None:
            raise HTTPException(
                status_code=400,
                detail={"message": "missing required field 'scout_output'"},
            )

        return certify_lead(
            scout_output=scout_output,
            template_id=payload.get("template_id", "sales_email"),
            template_version=payload.get("template_version", "v0.1"),
            mnemosyne_store=store,
        )

    @router.get(f"{prefix_within_router}/health")
    async def health() -> Dict[str, Any]:
        return {
            "status":         "ok",
            "modules_loaded": len(FOUNDATION_COMPONENTS),
            "mnemosyne_attached": store is not None,
        }

    return router
