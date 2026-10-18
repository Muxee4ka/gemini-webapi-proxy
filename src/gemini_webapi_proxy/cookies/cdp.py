"""Read cookies from a long-running Chrome over the DevTools protocol (CDP).

The browser owns the Google session: it keeps ``__Secure-1PSIDTS`` fresh the
way a real user's browser does (including device-bound session refreshes,
which a copied cookie jar cannot do).  The proxy only reads the current jar,
so it must not rotate cookies itself; see :mod:`gemini_webapi_proxy.client.pool`.

Before reading, the Gemini tab is reloaded so the browser refreshes the
session cookies if they are due.
"""

from __future__ import annotations

import logging

from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.cookies.base import BaseCookieSource, CookieBundle, register_source

logger = logging.getLogger(__name__)

GEMINI_URL = "https://gemini.google.com/app"


async def read_cdp_cookies(cdp_url: str, *, settle_ms: int = 5000) -> dict[str, str]:
    """Reload (or open) the Gemini tab in the browser at ``cdp_url`` and return its cookies."""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=30_000)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = next((p for p in ctx.pages if "gemini.google.com" in p.url), None)
        try:
            if page is None:
                page = await ctx.new_page()
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
            else:
                await page.reload(wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(settle_ms)
        except Exception as exc:  # cookies may still be usable; read them anyway
            logger.warning("CDP: reloading the Gemini tab failed: %s", exc)
        cookies = await ctx.cookies(["https://gemini.google.com"])
        return {c["name"]: c["value"] for c in cookies}
    finally:
        # Stopping the driver drops the CDP connection; the browser keeps running.
        await pw.stop()


@register_source
class CdpCookieSource(BaseCookieSource):
    """Use the Google session of a Chrome reachable at ``GOP_CDP_URL``."""

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
