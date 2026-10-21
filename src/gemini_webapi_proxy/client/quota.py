"""The account's usage limits as gemini-webapi keeps them on the client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


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
