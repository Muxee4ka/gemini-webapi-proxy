"""``GET /admin/quota`` shapes the limits gemini-webapi keeps on the client."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from gemini_webapi.constants import AccountStatus

from gemini_webapi_proxy.routes import admin

USAGE = {
    "tier": {"id": 3, "label": "ULTRA"},
    "use_overage_ai_credits": False,
    "current_5h": {"window": "5h", "remaining_credits": 900, "usage_percentage": 10},
    "weekly": None,
}

CLIENT = SimpleNamespace(
    account_status=AccountStatus.AVAILABLE,
    abuse_status={"status": "Clean"},
    usage_info=USAGE,
    quotas={
        "None-11": {
            "label": "Gemini Flash [None-11]",
            "remaining": 36685,
            "total": 48384,
            "usage_percentage": 24,
            "reset_time": 1792438762,
        },
        "extra": {"default": {"is_blocked": False, "usage_percentage": 5.0, "reset_time": None}},
        "usage_info": USAGE,
    },
)


def test_quota_report_shapes_client_limits() -> None:
    report = admin.quota_report(CLIENT)
    assert report["account_status"] == "AVAILABLE"
    assert report["tier"] == "ULTRA"
    assert report["usage"]["current_5h"]["remaining_credits"] == 900
    assert report["models"] == [
        {
            "label": "Gemini Flash [None-11]",
            "remaining": 36685,
            "total": 48384,
            "usage_percentage": 24,
            "reset_at": "2026-10-19T19:39:22+00:00",
        }
    ]
    assert report["extra"] == {"is_blocked": False, "usage_percentage": 5.0, "reset_time": None}


def test_quota_report_tolerates_a_fresh_client() -> None:
    report = admin.quota_report(SimpleNamespace())
    assert report["models"] == [] and report["usage"] == {} and report["extra"] is None


def test_quota_route(monkeypatch) -> None:
    from gemini_webapi_proxy.app import app

    async def get_client(settings):
        return CLIENT

    monkeypatch.setattr(admin, "get_client", get_client)
    with TestClient(app) as client:
        resp = client.get("/admin/quota")
    assert resp.status_code == 200
    assert resp.json()["models"][0]["remaining"] == 36685
