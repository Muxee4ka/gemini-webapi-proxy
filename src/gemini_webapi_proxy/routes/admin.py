"""Admin endpoints.

* ``POST /admin/probe-models`` — re-probe every model and refresh the registry.
* ``GET /admin/quota`` — the first usable account's limits (all accounts: ``GET /admin/accounts``).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from gemini_webapi_proxy.auth import require_admin
from gemini_webapi_proxy.client.pool import get_client, registry
from gemini_webapi_proxy.client.quota import quota_report
from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.services.probe import probe_all_models

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.post("/probe-models")
async def admin_probe_models():
    settings = get_settings()
    await probe_all_models(settings)
    return {
        "status": "ok",
        "models": registry.list_openai_models(),
        "updated_at": int(time.time()),
    }


@router.get("/quota")
async def admin_quota():
    client = await get_client(get_settings())
    return {**quota_report(client), "checked_at": int(time.time())}
