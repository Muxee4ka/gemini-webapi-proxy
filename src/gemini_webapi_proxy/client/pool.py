"""Process-wide Gemini client pool.

A single :class:`gemini_webapi.GeminiClient` is lazily initialised on the
first request and re-used thereafter.  The pool is the integration point
between the pluggable :mod:`gemini_webapi_proxy.cookies` sources and the
HTTP layer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from gemini_webapi import GeminiClient
from gemini_webapi.constants import AccountStatus

from gemini_webapi_proxy.client.registry import ModelRegistry
from gemini_webapi_proxy.config import Settings, get_settings
from gemini_webapi_proxy.cookies import build_source
from gemini_webapi_proxy.cookies.session_bootstrap import bootstrap_browser_cookie_session
from gemini_webapi_proxy.downloaders.image_export import sanitize_gemini_client_cookies

logger = logging.getLogger(__name__)

_client: GeminiClient | None = None
_client_created_at = 0.0
_lock = asyncio.Lock()
_background: set[asyncio.Task] = set()
_UNAUTH_RETRY_SECONDS = 60.0
registry = ModelRegistry()


async def _load_cookie_bundle(settings: Settings):
    """Resolve the configured cookie source to a :class:`CookieBundle`."""
    if settings.cookie_source == "browser":
        bootstrap_browser_cookie_session()
        bundle = await build_source("browser", browser=settings.browser).load()
        bootstrap_browser_cookie_session(psid=bundle.psid)
        return bundle
    # env / file: build_source() raises if the source is mis-configured
    return await build_source(settings.cookie_source).load()


def _client_authenticated(client: GeminiClient) -> bool:
    return getattr(client, "account_status", None) != AccountStatus.UNAUTHENTICATED


def _clear_library_cookie_cache() -> None:
    """Drop gemini-webapi's cached cookies.

    The library tries its cache before the cookies it is given.  With ``cdp`` the
    browser's jar is always the freshest, so a cached jar can only be older.
    """
    cache_dir = os.getenv("GEMINI_COOKIE_PATH") or Path(tempfile.gettempdir()) / "gemini_webapi"
    for path in Path(cache_dir).glob(".cached_cookies_*.json"):
        path.unlink(missing_ok=True)


def _close_later(client: GeminiClient, delay: float = 300.0) -> None:
    """Close a replaced client once requests that still hold it are done."""

    async def _close() -> None:
        await asyncio.sleep(delay)
        try:
            await client.close()
        except Exception:
            pass

    task = asyncio.create_task(_close())
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _new_client(settings: Settings) -> GeminiClient:
    bundle = await _load_cookie_bundle(settings)
    if not bundle.is_valid():
        raise RuntimeError("Cookie source returned an invalid bundle (no __Secure-1PSID)")

    client = GeminiClient(bundle.psid, bundle.psidts, proxy=settings.http_proxy)
    if bundle.extras:
        client.cookies = dict(bundle.extras)
    if settings.cookie_source == "cdp":
        # The browser owns the session and rotates its cookies; a second rotation
        # chain from here would fork it.
        _clear_library_cookie_cache()
        await client.init(timeout=settings.init_timeout, auto_refresh=False)
    else:
        await client.init(timeout=settings.init_timeout, auto_refresh=True)
    sanitize_gemini_client_cookies(client)
    return client


async def get_client(settings: Settings | None = None) -> GeminiClient:
    global _client, _client_created_at
    settings = settings or get_settings()
    if _client is not None and not _cdp_refresh_due(settings):
        return _client
    async with _lock:
        if _client is not None and not _cdp_refresh_due(settings):
            return _client

        old = _client
        try:
            client = await _new_client(settings)
        except Exception:
            if old is not None:  # keep serving with the previous jar
                logger.warning("Re-reading cookies failed; keeping the current client")
                _client_created_at = time.monotonic()
                return old
            raise

        _client, _client_created_at = client, time.monotonic()
        if old is not None:
            _close_later(old)
        registry.load_file()
        registry.sync_from_client(client.list_models())
        return _client


def _cdp_refresh_due(settings: Settings) -> bool:
    return (
        settings.cookie_source == "cdp"
        and time.monotonic() - _client_created_at >= settings.cdp_refresh_seconds
    )


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
    from gemini_webapi_proxy.downloaders.playwright_downloader import close_browser_fetch

    await close_browser_fetch()


async def invalidate_client() -> None:
    """Drop cached Gemini client so the next request re-inits (re-reads cookies).

    A client that came up unauthenticated is kept for a minute: re-initialising
    on every failing request only hammers Google with logged-out sessions.
    """
    global _client
    async with _lock:
        if _client is None:
            return
        if (
            not _client_authenticated(_client)
            and time.monotonic() - _client_created_at < _UNAUTH_RETRY_SECONDS
        ):
            return
        try:
            await _client.close()
        finally:
            _client = None


@asynccontextmanager
async def lifespan(app):  # type: ignore[no-untyped-def]
    registry.load_file()
    # Eagerly init the Gemini client during startup so the model
    # registry is populated before the first /openai/v1/models
    # request. Without this, the models endpoint returns an empty
    # list until a chat/image call forces lazy init.
    try:
        await get_client()
    except Exception:
        # Cookie not yet configured, or Gemini is down.  The first
        # real request will retry; we don't want startup to fail.
        pass
    yield
    await close_client()
