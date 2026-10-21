"""``cdp`` cookie source: reading the browser's session and the sign-in kiosk."""

from __future__ import annotations

import json

import pytest

from gemini_webapi_proxy.cookies import CookieBundle, build_source


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


class FakeBrowser:
    """Answers DevTools commands like a Chrome with one Gemini tab open."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._replies: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        results = {
            "Storage.getCookies": {
                "cookies": [
                    {
                        "name": "__Secure-1PSID",
                        "value": "sid",
                        "domain": ".google.com",
                        "path": "/",
                    },
                    {"name": "NID", "value": "wide", "domain": ".google.com", "path": "/"},
                    {"name": "NID", "value": "own", "domain": "gemini.google.com", "path": "/"},
                    {"name": "SID", "value": "x", "domain": "accounts.google.com", "path": "/"},
                    {"name": "P", "value": "x", "domain": ".google.com", "path": "/maps"},
                ]
            },
            "Target.getTargets": {
                "targetInfos": [
                    {"targetId": "t1", "type": "page", "url": "https://gemini.google.com/app"},
                    {"targetId": "t2", "type": "page", "url": "about:blank"},
                ]
            },
            "Target.attachToTarget": {"sessionId": "s1"},
        }
        # An event first: the client must skip it while waiting for its reply.
        self._replies.append(json.dumps({"method": "Target.targetInfoChanged", "params": {}}))
        self._replies.append(
            json.dumps({"id": msg["id"], "result": results.get(msg["method"], {})})
        )

    async def recv(self) -> str:
        return self._replies.pop(0)


async def test_read_cdp_cookies_takes_gemini_jar_and_parks_tabs(monkeypatch: pytest.MonkeyPatch):
    from gemini_webapi_proxy.cookies import cdp

    browser = FakeBrowser()
    monkeypatch.setattr(cdp.websockets, "connect", lambda url, **kw: browser)
    jar = await cdp.read_cdp_cookies("ws://127.0.0.1:9223/devtools/browser/x")

    assert jar == {"__Secure-1PSID": "sid", "NID": "own"}
    calls = [(m["method"], m.get("sessionId")) for m in browser.sent]
    assert calls == [
        ("Storage.getCookies", None),
        ("Target.getTargets", None),
        ("Target.attachToTarget", None),
        ("Page.navigate", "s1"),
        ("Target.detachFromTarget", None),
    ]
    assert browser.sent[2]["params"] == {"targetId": "t1", "flatten": True}


async def test_take_session_reads_then_clears_the_browser(monkeypatch: pytest.MonkeyPatch):
    from gemini_webapi_proxy.cookies import cdp

    browser = FakeBrowser()
    monkeypatch.setattr(cdp.websockets, "connect", lambda url, **kw: browser)
    jar, email = await cdp.take_session("ws://127.0.0.1:9223/devtools/browser/x")

    assert jar == {"__Secure-1PSID": "sid", "NID": "own"}
    assert email is None  # the fake page has no avatar
    methods = [m["method"] for m in browser.sent]
    assert "Page.navigate" in methods
    assert methods[-1] == "Storage.clearCookies"


async def test_open_sign_in_starts_from_a_clean_browser(monkeypatch: pytest.MonkeyPatch):
    from gemini_webapi_proxy.cookies import cdp

    browser = FakeBrowser()
    monkeypatch.setattr(cdp.websockets, "connect", lambda url, **kw: browser)
    await cdp.open_sign_in("ws://127.0.0.1:9223/devtools/browser/x")

    methods = [m["method"] for m in browser.sent]
    assert methods[0] == "Storage.clearCookies"
    assert "Target.closeTarget" in methods  # the second tab
    nav = next(m for m in browser.sent if m["method"] == "Page.navigate")
    assert nav["params"]["url"] == cdp.SIGN_IN_URL
