"""Unit tests for updates: build info and the GitHub comparison."""

from __future__ import annotations

import json

import httpx
import pytest

from gemini_webapi_proxy import events, updates
from gemini_webapi_proxy.config import Settings

BUILD = "a" * 40
LIB = "b" * 40


def _commit(sha: str, message: str) -> dict:
    return {
        "sha": sha * 40,
        "html_url": f"https://github.com/x/commit/{sha}",
        "commit": {"message": f"{message}\n\nbody", "committer": {"date": "2026-10-21T10:00:00Z"}},
    }


def _settings(tmp_path, **kw) -> Settings:
    return Settings(data_dir=tmp_path, build_commit=BUILD, build_branch="accounts-wip", **kw)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    events.reset()
    updates.state.clear()
    monkeypatch.setattr(updates, "_library_commit", lambda: LIB)
    monkeypatch.setattr(updates.metadata, "version", lambda name: "2.1.1.post2")
    yield
    events.reset()
    updates.state.clear()


def _github(monkeypatch, routes: dict) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path not in routes:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=routes[path])

    real = httpx.AsyncClient

    def client(**kwargs):
        kwargs.pop("proxy", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(updates.httpx, "AsyncClient", client)
    return seen


ROUTES = {
    f"/repos/Muxee4ka/gemini-webapi-proxy/compare/{BUILD}...accounts-wip": {
        "ahead_by": 2,
        "html_url": "https://github.com/cmp",
        "commits": [_commit("1", "first"), _commit("2", "second")],
    },
    "/repos/HanaokaYuzu/Gemini-API/releases/latest": {
        "tag_name": "v2.2.0",
        "published_at": "2026-10-20T00:00:00Z",
        "html_url": "https://github.com/rel",
    },
    f"/repos/HanaokaYuzu/Gemini-API/compare/{LIB}...HEAD": {"ahead_by": 0, "commits": []},
    "/repos/Lutiancheng1/gemini-webapi-proxy/compare/Muxee4ka:accounts-wip...HEAD": {
        "ahead_by": 0,
        "commits": [],
    },
}


def test_build_info(tmp_path) -> None:
    info = updates.build_info(_settings(tmp_path, build_date="2026-10-21T20:00:00Z"))
    assert info["commit"] == BUILD and info["branch"] == "accounts-wip"
    assert info["gemini_webapi"] == {"version": "2.1.1.post2", "commit": LIB}


def test_new_commits_plural() -> None:
    assert [updates._new_commits(n) for n in (1, 2, 5, 11, 21, 22, 112)] == [
        "1 новый коммит",
        "2 новых коммита",
        "5 новых коммитов",
        "11 новых коммитов",
        "21 новый коммит",
        "22 новых коммита",
        "112 новых коммитов",
    ]


def test_release_tuple_ignores_post_releases() -> None:
    assert updates._release_tuple("v2.1.1") == updates._release_tuple("2.1.1.post2") == (2, 1, 1)
    assert updates._release_tuple("v2.2.0") > updates._release_tuple("2.1.1.post2")


async def test_check_reports_once_and_saves(monkeypatch, tmp_path) -> None:
    _github(monkeypatch, ROUTES)
    alerts: list[events.Event] = []
    events.subscribe(alerts.append)
    try:
        result = await updates.check(_settings(tmp_path))
        assert result["proxy"]["behind"] == 2
        assert [c["message"] for c in result["proxy"]["commits"]] == ["second", "first"]
        assert result["library"]["release_newer"] is True
        assert result["upstream"]["behind"] == 0
        assert result["available"] == [
            "прокси: 2 новых коммита в accounts-wip",
            "gemini-webapi: релиз v2.2.0",
        ]
        assert json.loads((tmp_path / "updates.json").read_text())["proxy"]["behind"] == 2

        events.reset()  # no throttling: the second check must stay quiet on its own
        await updates.check(_settings(tmp_path))
        assert [a.kind for a in alerts] == ["update"]
    finally:
        events.unsubscribe(alerts.append)


async def test_check_without_build_commit_shows_branch_head(monkeypatch, tmp_path) -> None:
    routes = dict(ROUTES)
    routes["/repos/Muxee4ka/gemini-webapi-proxy/commits/main"] = _commit("3", "head")
    _github(monkeypatch, routes)
    result = await updates.check(Settings(data_dir=tmp_path))
    assert result["proxy"]["behind"] is None
    assert result["proxy"]["commits"][0]["message"] == "head"
    assert result["available"] == ["gemini-webapi: релиз v2.2.0"]


async def test_one_failing_part_does_not_hide_the_others(monkeypatch, tmp_path) -> None:
    routes = dict(ROUTES)
    del routes["/repos/Lutiancheng1/gemini-webapi-proxy/compare/Muxee4ka:accounts-wip...HEAD"]
    _github(monkeypatch, routes)
    result = await updates.check(_settings(tmp_path))
    assert "404" in result["upstream"]["error"]
    assert result["proxy"]["behind"] == 2
