"""Admin endpoints.

* ``POST /admin/probe-models`` — re-probe every model and refresh the registry.
* ``GET /admin/quota`` — the account's usage limits as gemini-webapi last saw them.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from gemini_webapi_proxy.auth import require_api_key_if_configured
from gemini_webapi_proxy.client.pool import get_client, registry
from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.services.probe import probe_all_models

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_api_key_if_configured)],
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


def _iso(ts: Any) -> str | None:
    if not isinstance(ts, (int, float)) or not ts:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def quota_report(client: Any) -> dict[str, Any]:
    """Shape the limits gemini-webapi keeps on the client.

    Nothing is fetched here: the library reads the per-model credits on every client
    init and the 5-hour / weekly usage after every generation.  Gemini does not report
    the video limit, it only shows up as a failed video job.
    """
    quotas = dict(getattr(client, "quotas", None) or {})
    usage = dict(getattr(client, "usage_info", None) or quotas.pop("usage_info", None) or {})
    quotas.pop("usage_info", None)
    extra = (quotas.pop("extra", None) or {}).get("default")
    status = getattr(client, "account_status", None)
    return {
        "account_status": getattr(status, "name", status),
        "tier": (usage.get("tier") or {}).get("label"),
        "usage": {
            key: usage.get(key)
            for key in ("current_5h", "weekly", "ai_credits_remaining", "use_overage_ai_credits")
            if key in usage
        },
        "models": [
            {
                "label": q.get("label"),
                "remaining": q.get("remaining"),
                "total": q.get("total"),
                "usage_percentage": q.get("usage_percentage"),
                "reset_at": _iso(q.get("reset_time")),
            }
            for q in quotas.values()
            if isinstance(q, dict)
        ],
        "extra": extra and {**extra, "reset_time": _iso(extra.get("reset_time"))},
        "abuse_status": getattr(client, "abuse_status", None),
    }


@router.get("/quota")
async def admin_quota():
    client = await get_client(get_settings())
    return {**quota_report(client), "checked_at": int(time.time())}
