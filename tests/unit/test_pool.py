"""The account pool: picking an account, moving on when one is used up or logged out."""

from __future__ import annotations

import time

import pytest
from gemini_webapi.constants import AccountStatus
from gemini_webapi.exceptions import UsageLimitExceededError

from gemini_webapi_proxy.client import pool as pool_module
from gemini_webapi_proxy.client.accounts import AccountStore
from gemini_webapi_proxy.client.base import ModelDescriptor
from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.errors import NoAccountError, QuotaExceededError


def _model(name: str) -> ModelDescriptor:
    return ModelDescriptor(name=name, display_name=name, description="")


class FakeClient:
    def __init__(self, account_id: str, models: list[str]) -> None:
        self.account_id = account_id
        self.account_status = AccountStatus.AVAILABLE
        self.models = [_model(m) for m in models]
        self.quotas: dict = {}
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    def list_models(self) -> list:
        return self.models


@pytest.fixture
def accounts(monkeypatch: pytest.MonkeyPatch):
    """Two signed-in accounts: ``free`` (first) has Flash only, ``pro`` has Pro too."""
    settings = get_settings()
    store = AccountStore(settings.data_dir / "accounts.json")
    store.remove("default")
    for name in ("free", "pro"):
        store.add(name)
        store.set_cookies(name, {"__Secure-1PSID": f"sid-{name}"}, None)
    models = {"free": ["gemini-flash"], "pro": ["gemini-flash", "gemini-pro"]}
    made: list[FakeClient] = []

    async def fake_new_client(settings, account):
        client = FakeClient(account.id, models[account.id])
        made.append(client)
        return client

    monkeypatch.setattr(pool_module, "_new_client", fake_new_client)
    monkeypatch.setattr(pool_module, "pool", pool_module.AccountPool())
    monkeypatch.setattr(pool_module, "registry", pool_module.ModelRegistry())
    return made


async def _who(settings, model: str) -> str:
    async def call(client, runtime):
        return client.account_id

    return await pool_module.pool.run(settings, model, call)


async def test_takes_the_first_account_that_has_the_model(accounts):
    settings = get_settings()
    assert await _who(settings, "gemini-flash") == "free"
    assert await _who(settings, "gemini-pro") == "pro"


async def test_quota_moves_on_and_cools_the_account_down(accounts):
    settings = get_settings()
    calls: list[str] = []

    async def call(client, runtime):
        calls.append(client.account_id)
        if client.account_id == "free":
            raise QuotaExceededError("You've reached your limit")
        return "answer"

    assert await pool_module.pool.run(settings, "gemini-flash", call) == "answer"
    assert calls == ["free", "pro"]
    free = pool_module.pool.slots["free"]
    assert free.cooling("gemini-flash")
    until = free.cooldowns["gemini-flash"]
    assert until - time.time() == pytest.approx(settings.quota_cooldown, abs=5)
    # The next request goes straight to the other account; other models are not affected.
    assert await _who(settings, "gemini-flash") == "pro"
    assert not free.cooling("gemini-pro")


async def test_cooldown_ends_at_the_reported_reset(accounts):
    settings = get_settings()
    reset = time.time() + 3600

    async def call(client, runtime):
        if client.account_id == "free":
            client.quotas = {"m": {"remaining": 0, "reset_time": reset}}
            raise UsageLimitExceededError("Usage limit exceeded for model 'Gemini Flash'.")
        return "ok"

    await pool_module.pool.run(settings, "gemini-flash", call)
    assert pool_module.pool.slots["free"].cooldowns["gemini-flash"] == reset


async def test_every_account_used_up_is_a_quota_error(accounts):
    async def call(client, runtime):
        raise QuotaExceededError("limit")

    with pytest.raises(QuotaExceededError):
        await pool_module.pool.run(get_settings(), "gemini-flash", call)


async def test_lost_session_moves_on_and_asks_for_sign_in(accounts):
    settings = get_settings()

    async def call(client, runtime):
        if client.account_id == "free":
            client.account_status = AccountStatus.UNAUTHENTICATED
            raise RuntimeError("Permission denied")
        return "ok"

    assert await pool_module.pool.run(settings, "gemini-flash", call) == "ok"
    free = pool_module.pool.slots["free"]
    assert free.state() == "needs_login"
    assert await _who(settings, "gemini-flash") == "pro"
    assert len(accounts) == 2  # a stored jar is not re-read: it would not have changed


async def test_other_errors_are_the_answer(accounts):
    async def call(client, runtime):
        raise RuntimeError("I cannot fulfill this request")

    with pytest.raises(RuntimeError, match="cannot fulfill"):
        await pool_module.pool.run(get_settings(), "gemini-flash", call)
    assert pool_module.pool.slots["free"].requests == 1
    assert pool_module.pool.slots["pro"].requests == 0


async def test_disabled_and_reordered_accounts(accounts):
    settings = get_settings()
    store = pool_module.pool.configure(settings)
    store.update("pro", priority=-1)
    assert await _who(settings, "gemini-flash") == "pro"
    store.update("pro", enabled=False)
    assert await _who(settings, "gemini-flash") == "free"


async def test_nobody_signed_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pool_module, "pool", pool_module.AccountPool())
    store = AccountStore(get_settings().data_dir / "accounts.json")
    store.remove("default")
    store.add("empty")

    async def call(client, runtime):
        raise AssertionError("not reached")

    with pytest.raises(NoAccountError, match="empty=needs_login"):
        await pool_module.pool.run(get_settings(), "gemini-flash", call)


async def test_settings_account_guest_is_re_read_after_a_minute(monkeypatch: pytest.MonkeyPatch):
    """The first-start ``default`` account follows GOP_COOKIE_SOURCE like before."""
    made: list[FakeClient] = []

    async def fake_new_client(settings, account):
        client = FakeClient(account.id, ["gemini-flash-lite"])
        client.account_status = AccountStatus.UNAUTHENTICATED
        made.append(client)
        return client

    monkeypatch.setattr(pool_module, "_new_client", fake_new_client)
    monkeypatch.setattr(pool_module, "pool", pool_module.AccountPool())
    settings = get_settings()
    first = await pool_module.get_client(settings)  # a guest is still handed out
    assert first is made[0]
    slot = pool_module.pool.slots["default"]
    assert slot.state() == "logged_out"
    await slot.ensure(settings)
    assert len(made) == 1

    slot.created_at -= 61
    await slot.ensure(settings)
    assert len(made) == 2 and made[0].closed


async def test_failed_init_keeps_the_current_client(monkeypatch: pytest.MonkeyPatch, accounts):
    settings = get_settings()
    await _who(settings, "gemini-flash")
    slot = pool_module.pool.slots["free"]
    first = slot.client
    slot.account.source = "settings"
    first.account_status = AccountStatus.UNAUTHENTICATED
    slot.created_at -= 61

    async def broken(settings, account):
        raise RuntimeError("browser is down")

    monkeypatch.setattr(pool_module, "_new_client", broken)
    assert await slot.ensure(settings) is first
    assert slot.error == "browser is down"


async def test_drop_lets_a_signed_in_account_start_again(accounts):
    settings = get_settings()
    await _who(settings, "gemini-flash")
    slot = pool_module.pool.slots["free"]
    slot.logged_out("gone")
    assert slot.state() == "needs_login"
    await pool_module.pool.drop("free")
    assert slot.state() == "idle"
    assert await _who(settings, "gemini-flash") == "free"
    assert [c.account_id for c in accounts] == ["free", "free"]


async def test_a_model_the_account_never_had_costs_no_refresh(accounts):
    settings = get_settings()
    await _who(settings, "gemini-flash")
    free = pool_module.pool.slots["free"]
    refreshed: list[int] = []

    async def fetch_user_status():
        refreshed.append(1)

    free.client._fetch_user_status = fetch_user_status
    free.models_refreshed_at = -1e9
    assert await _who(settings, "gemini-pro") == "pro"
    assert refreshed == []
    # Flash it had: missing now (exhausted at init) -> the list is re-read.
    free.client.models = []
    await free.runtime("gemini-flash")
    assert refreshed == [1]


async def test_calls_go_through_the_account_proxy_and_are_paced(accounts, monkeypatch):
    from gemini_webapi_proxy.egress import egress

    settings = get_settings().model_copy(update={"account_min_interval": 0.3})
    store = pool_module.pool.configure(settings)
    store.update("free", proxy="http://127.0.0.1:10812")
    seen: list = []

    async def call(client, runtime):
        seen.append((client.account_id, egress.get(), time.monotonic()))
        return "ok"

    await pool_module.pool.run(settings, "gemini-flash", call)
    await pool_module.pool.run(settings, "gemini-flash", call)
    await pool_module.pool.run(settings, "gemini-pro", call)
    assert [s[:2] for s in seen] == [
        ("free", "http://127.0.0.1:10812"),
        ("free", "http://127.0.0.1:10812"),
        ("pro", None),
    ]
    assert seen[1][2] - seen[0][2] >= settings.account_min_interval - 0.05
    assert egress.get() is None


async def test_an_account_flagged_for_abuse_is_switched_off(accounts):
    settings = get_settings()
    await _who(settings, "gemini-flash")
    free = pool_module.pool.slots["free"]
    free.client.abuse_status = {"is_clean": False, "status_code": 3}
    assert await _who(settings, "gemini-flash") == "pro"
    assert free.account.enabled is False and "flagged" in free.error
    assert AccountStore(settings.data_dir / "accounts.json").get("free").enabled is False


def test_redirects_through_a_local_proxy_are_followed():
    """gemini-webapi's SAFE redirects reject everything behind a proxy on 127.0.0.1."""
    import importlib

    from curl_cffi import CurlFollow

    from gemini_webapi_proxy.client import curl_compat

    for name in curl_compat._MODULES:
        assert importlib.import_module(name).AsyncSession is curl_compat.LocalProxySession
    assert curl_compat.local_proxy("http://127.0.0.1:10812")
    assert curl_compat.local_proxy("socks5h://10.0.0.5:1080")
    assert not curl_compat.local_proxy("http://193.180.210.131:3128")
    assert not curl_compat.local_proxy(None)

    seen = {}

    class Probe(curl_compat.LocalProxySession):
        def __init__(self, *args, **kwargs):
            curl_compat.LocalProxySession.__init__(self, *args, **kwargs)
            seen[kwargs.get("proxy")] = self.allow_redirects

    Probe(proxy="http://127.0.0.1:10812", allow_redirects=CurlFollow.SAFE)
    Probe(proxy="http://193.180.210.131:3128", allow_redirects=CurlFollow.SAFE)
    Probe(proxy=None, allow_redirects=CurlFollow.SAFE)
    assert seen["http://127.0.0.1:10812"] is True
    assert seen["http://193.180.210.131:3128"] == CurlFollow.SAFE
    assert seen[None] == CurlFollow.SAFE
