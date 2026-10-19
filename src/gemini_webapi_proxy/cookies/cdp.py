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

import logging

from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.cookies.base import BaseCookieSource, CookieBundle, register_source

logger = logging.getLogger(__name__)


async def read_cdp_cookies(cdp_url: str) -> dict[str, str]:
    """Return the browser's Gemini cookies and park its Google tabs on about:blank."""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        if not browser.contexts:
            return {}
        ctx = browser.contexts[0]
        cookies = await ctx.cookies(["https://gemini.google.com"])
        jar = {c["name"]: c["value"] for c in cookies}
        if "__Secure-1PSID" in jar:
            for page in ctx.pages:
                if "google.com" in page.url:
                    try:
                        await page.goto("about:blank")
                    except Exception as exc:
                        logger.warning("CDP: could not leave %s: %s", page.url, exc)
        return jar
    finally:
        # Stopping the driver drops the CDP connection; the browser keeps running.
        await pw.stop()


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
