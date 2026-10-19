"""``cdp`` cookie source and the client pool's handling of logged-out sessions."""

from __future__ import annotations

import pytest
from gemini_webapi.constants import AccountStatus

from gemini_webapi_proxy.client import pool
from gemini_webapi_proxy.cookies import CookieBundle, build_source


class FakeClient:
    def __init__(self, status: AccountStatus = AccountStatus.AVAILABLE) -> None:
        self.account_status = status
        self.closed = False
        self.init_kwargs: dict = {}

    async def init(self, **kwargs) -> None:
        self.init_kwargs = kwargs

    async def close(self) -> None:
        self.closed = True

    def list_models(self) -> list:
        return []


@pytest.fixture
def fresh_pool(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pool, "_client", None)
    monkeypatch.setattr(pool, "_client_created_at", 0.0)
    monkeypatch.setattr(pool, "sanitize_gemini_client_cookies", lambda c: None)
    made: list[FakeClient] = []

    async def fake_new_client(settings):
        client = FakeClient()
        await client.init(auto_refresh=True)
        made.append(client)
        return client

    monkeypatch.setattr(pool, "_new_client", fake_new_client)
    return made


async def test_cdp_source_reads_browser_cookies(monkeypatch: pytest.MonkeyPatch):
    from gemini_webapi_proxy.cookies import cdp

    async def fake_read(url: str):
        assert url == "http://127.0.0.1:9333"
        return {"__Secure-1PSID": "sid", "__Secure-1PSIDTS": "ts", "NID": "n"}

    monkeypatch.setenv("GOP_CDP_URL", "http://127.0.0.1:9333")
    monkeypatch.setattr(cdp, "read_cdp_cookies", fake_read)
    bundle = await build_source("cdp").load()
    assert bundle == CookieBundle(
        "sid", "ts", {"__Secure-1PSID": "sid", "__Secure-1PSIDTS": "ts", "NID": "n"}
    )


async def test_cdp_source_requires_signed_in_browser(monkeypatch: pytest.MonkeyPatch):
    from gemini_webapi_proxy.cookies import cdp

    async def fake_read(url: str):
        return {"NID": "n"}

    monkeypatch.setattr(cdp, "read_cdp_cookies", fake_read)
    with pytest.raises(RuntimeError, match="not signed in"):
        await build_source("cdp").load()


async def test_client_is_reused(fresh_pool):
    first = await pool.get_client()
    assert first.init_kwargs == {"auto_refresh": True}
    assert await pool.get_client() is first
    assert len(fresh_pool) == 1


async def test_guest_client_is_replaced_after_a_minute(monkeypatch, fresh_pool):
    first = await pool.get_client()
    first.account_status = AccountStatus.UNAUTHENTICATED
    assert await pool.get_client() is first

    monkeypatch.setattr(pool, "_client_created_at", pool._client_created_at - 61)
    second = await pool.get_client()
    assert second is not first and first.closed


async def test_failed_reinit_keeps_current_client(monkeypatch, fresh_pool):
    first = await pool.get_client()
    first.account_status = AccountStatus.UNAUTHENTICATED
    monkeypatch.setattr(pool, "_client_created_at", -10_000.0)

    async def broken(settings):
        raise RuntimeError("browser is down")

    monkeypatch.setattr(pool, "_new_client", broken)
    assert await pool.get_client() is first


async def test_unauthenticated_client_is_not_reinitialised_within_a_minute(monkeypatch, fresh_pool):
    client = await pool.get_client()
    client.account_status = AccountStatus.UNAUTHENTICATED
    await pool.invalidate_client()
    assert pool._client is client and not client.closed

    monkeypatch.setattr(pool, "_client_created_at", pool._client_created_at - 61)
    await pool.invalidate_client()
    assert pool._client is None and client.closed
