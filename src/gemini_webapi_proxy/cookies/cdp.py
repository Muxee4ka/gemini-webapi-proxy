"""Take the Google session from a Chrome on the server over DevTools (CDP).

The browser is only where a person signs in: it runs on the same host as the
proxy, so the session is created there and not bound to a desktop device.  The
proxy reads the cookie jar once per client init and from then on owns the
session (gemini-webapi rotates ``__Secure-1PSIDTS``).

A session used by two clients at once forks: each gets its own rotated
``__Secure-1PSIDTS`` and Google ends up revoking the session.  So after reading,
every Google tab in the browser is switched to ``about:blank`` and the browser
stays off Gemini until the next sign-in.
"""

from __future__ import annotations

import itertools
import json
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
import websockets

from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.cookies.base import BaseCookieSource, CookieBundle, register_source

logger = logging.getLogger(__name__)

_GEMINI_HOST = "gemini.google.com"


class _Cdp:
    """Bare DevTools protocol over the browser websocket: commands only, events dropped."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._ids = itertools.count(1)

    async def send(self, method: str, params: dict | None = None, session: str | None = None):
        msg_id = next(self._ids)
        msg: dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        await self._ws.send(json.dumps(msg))
        while True:
            reply = json.loads(await self._ws.recv())
            if reply.get("id") != msg_id:
                continue
            if "error" in reply:
                raise RuntimeError(f"CDP {method}: {reply['error'].get('message')}")
            return reply.get("result", {})


async def _browser_ws_url(cdp_url: str) -> str:
    if cdp_url.startswith(("ws://", "wss://")):
        return cdp_url
    async with httpx.AsyncClient(timeout=30, trust_env=False) as http:
        resp = await http.get(cdp_url.rstrip("/") + "/json/version")
        resp.raise_for_status()
        return resp.json()["webSocketDebuggerUrl"]


def _sent_to_gemini(cookie: dict) -> bool:
    """Whether the browser would send ``cookie`` with a request to https://gemini.google.com/."""
    domain = cookie.get("domain", "")
    if domain.startswith("."):
        host_ok = domain[1:] == _GEMINI_HOST or _GEMINI_HOST.endswith(domain)
    else:
        host_ok = domain == _GEMINI_HOST
    return host_ok and cookie.get("path", "/") == "/"


async def _gemini_jar(cdp: _Cdp, context: str | None = None) -> dict[str, str]:
    params = {"browserContextId": context} if context else {}
    cookies = [
        c for c in (await cdp.send("Storage.getCookies", params))["cookies"] if _sent_to_gemini(c)
    ]
    # A host-only cookie wins over a same-named one set for .google.com.
    cookies.sort(key=lambda c: not c["domain"].startswith("."))
    return {c["name"]: c["value"] for c in cookies}


async def _pages(cdp: _Cdp) -> list[dict]:
    return [t for t in (await cdp.send("Target.getTargets"))["targetInfos"] if t["type"] == "page"]


async def _on_page(cdp: _Cdp, target: dict, method: str, params: dict) -> dict:
    attached = await cdp.send(
        "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
    )
    session = attached["sessionId"]
    try:
        return await cdp.send(method, params, session)
    finally:
        await cdp.send("Target.detachFromTarget", {"sessionId": session})


async def _park_google_tabs(cdp: _Cdp) -> None:
    for target in await _pages(cdp):
        if "google.com" not in target["url"]:
            continue
        try:
            await _on_page(cdp, target, "Page.navigate", {"url": "about:blank"})
        except Exception as exc:
            logger.warning("CDP: could not leave %s: %s", target["url"], exc)


_EMAIL_JS = r"""(() => {
  for (const el of document.querySelectorAll('[aria-label]')) {
    const m = el.getAttribute('aria-label').match(/[\w.+-]+@[\w-]+(\.[\w-]+)+/);
    if (m) return m[0];
  }
  return null;
})()"""


async def _signed_in_email(cdp: _Cdp, context: str | None = None) -> str | None:
    """The account shown on an open Gemini page (its avatar button names it)."""
    for target in await _pages(cdp):
        if _GEMINI_HOST not in target["url"]:
            continue
        if context and target.get("browserContextId") != context:
            continue
        try:
            result = await _on_page(
                cdp, target, "Runtime.evaluate", {"expression": _EMAIL_JS, "returnByValue": True}
            )
        except Exception:
            continue
        value = (result.get("result") or {}).get("value")
        if isinstance(value, str):
            return value
    return None


async def read_cdp_cookies(cdp_url: str) -> dict[str, str]:
    """Return the browser's Gemini cookies and park its Google tabs on about:blank."""
    async with websockets.connect(await _browser_ws_url(cdp_url), max_size=None) as ws:
        cdp = _Cdp(ws)
        jar = await _gemini_jar(cdp)
        if "__Secure-1PSID" in jar:
            await _park_google_tabs(cdp)
        # Closing the socket only drops the connection; the browser keeps running.
        return jar


SIGN_IN_URL = (
    "https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fgemini.google.com%2Fapp"
)


def chrome_proxy(proxy: str | None) -> str | None:
    """``proxy`` as Chrome's ``proxyServer`` takes it (it cannot carry a login)."""
    if not proxy:
        return None
    parts = urlsplit(proxy)
    if parts.username or parts.password:
        raise ValueError(
            "The sign-in browser cannot use a proxy with a login: Chrome takes no "
            "credentials in the proxy address. Use a local proxy without one."
        )
    scheme = "socks5" if parts.scheme.startswith("socks") else parts.scheme or "http"
    return f"{scheme}://{parts.hostname}:{parts.port}"


async def _sign_in_contexts(cdp: _Cdp) -> list[str]:
    return list((await cdp.send("Target.getBrowserContexts")).get("browserContextIds", []))


async def _dispose_contexts(cdp: _Cdp) -> None:
    for context in await _sign_in_contexts(cdp):
        try:
            await cdp.send("Target.disposeBrowserContext", {"browserContextId": context})
        except Exception as exc:
            logger.warning("CDP: could not dispose context %s: %s", context, exc)


async def open_sign_in(cdp_url: str, proxy: str | None = None) -> str:
    """Start a sign-in in a fresh, separate browser context (own cookies, own proxy).

    The context lives in memory only and is disposed once the session is taken, so
    the browser keeps nothing.  With ``proxy`` Google sees the sign-in come from the
    address the account will be used from.  Returns the context id.
    """
    proxy_server = chrome_proxy(proxy)
    async with websockets.connect(await _browser_ws_url(cdp_url), max_size=None) as ws:
        cdp = _Cdp(ws)
        await _dispose_contexts(cdp)  # an earlier sign-in left unfinished
        await cdp.send("Storage.clearCookies")  # the default context holds nothing either
        params: dict[str, Any] = {"disposeOnDetach": False}
        if proxy_server:
            params["proxyServer"] = proxy_server
        context = (await cdp.send("Target.createBrowserContext", params))["browserContextId"]
        target = await cdp.send(
            "Target.createTarget",
            {"url": SIGN_IN_URL, "browserContextId": context, "newWindow": True},
        )
        try:  # fill the virtual screen, which is what noVNC shows
            window = await cdp.send("Browser.getWindowForTarget", {"targetId": target["targetId"]})
            await cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window["windowId"],
                    "bounds": {"left": 0, "top": 0, "width": 1280, "height": 900},
                },
            )
        except Exception as exc:
            logger.debug("CDP: could not place the sign-in window: %s", exc)
        return context


async def take_session(
    cdp_url: str, context: str | None = None
) -> tuple[dict[str, str], str | None]:
    """Hand the signed-in session over to the proxy: the browser keeps no copy.

    A Chrome that still holds the cookies keeps talking to Google in the background
    (account checks, cookie rotation) even with no Google tab open, and a session
    rotated by two clients gets revoked.  So the jar is read and the sign-in context
    is disposed (or, for a sign-in in the default context, every cookie cleared and
    the tabs sent off Google).  Dropping local cookies is not a sign-out: the session
    stays valid, now held only by the proxy.
    """
    async with websockets.connect(await _browser_ws_url(cdp_url), max_size=None) as ws:
        cdp = _Cdp(ws)
        if context is None:  # the proxy restarted mid sign-in: take the one left open
            contexts = await _sign_in_contexts(cdp)
            context = contexts[0] if len(contexts) == 1 else None
        jar = await _gemini_jar(cdp, context)
        if "__Secure-1PSID" not in jar:
            return jar, None
        email = await _signed_in_email(cdp, context)
        if context:
            await cdp.send("Target.disposeBrowserContext", {"browserContextId": context})
        else:
            await _park_google_tabs(cdp)
            await cdp.send("Storage.clearCookies")
        return jar, email


async def cancel_sign_in(cdp_url: str) -> None:
    async with websockets.connect(await _browser_ws_url(cdp_url), max_size=None) as ws:
        cdp = _Cdp(ws)
        await _dispose_contexts(cdp)
        await cdp.send("Storage.clearCookies")


@register_source
class CdpCookieSource(BaseCookieSource):
    """Use the Google session signed in to the Chrome at ``GOP_CDP_URL``."""

    name = "cdp"

    async def load(self) -> CookieBundle:
        url = get_settings().cdp_url
        cookie_map = await read_cdp_cookies(url)
        psid = cookie_map.get("__Secure-1PSID", "")
        if not psid:
            raise RuntimeError(
                f"The browser at {url} is not signed in to Google: no __Secure-1PSID. "
                "Sign in to gemini.google.com in it (see docs/cookie-sources.md)."
            )
        return CookieBundle(
            psid=psid,
            psidts=cookie_map.get("__Secure-1PSIDTS", ""),
            extras=cookie_map,
        )

    async def health_check(self, bundle: CookieBundle, proxy: str | None) -> bool:
        return True
