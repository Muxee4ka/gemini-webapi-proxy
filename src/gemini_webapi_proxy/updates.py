"""Which code is running, and what is newer on GitHub (admin UI → Система, Telegram).

The proxy's commit and branch come from the Docker build (``GOP_BUILD_COMMIT`` /
``GOP_BUILD_BRANCH``); gemini-webapi's commit from pip's ``direct_url.json`` when it
was installed from git.  Three things are compared on GitHub:

* this proxy: commits on the followed branch after the build commit;
* gemini-webapi: a release newer than the installed version, and commits on its
  default branch after the pinned one;
* the original proxy (``upstream_repo``): its commits this fork does not have.

GitHub is asked from a background task every ``update_check_hours`` and from the UI
button; the last result is kept in ``data/updates.json`` so a restart shows it at once.
A new finding is reported once as an ``update`` event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from importlib import metadata
from typing import Any

import httpx

from gemini_webapi_proxy import __version__, events
from gemini_webapi_proxy.config import Settings

log = logging.getLogger(__name__)

API = "https://api.github.com"
FIRST_CHECK_DELAY = 60.0  # let the accounts come up first
MAX_COMMITS = 10

state: dict[str, Any] = {}
_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def _library_commit() -> str | None:
    try:
        raw = metadata.distribution("gemini-webapi").read_text("direct_url.json")
        return json.loads(raw or "{}").get("vcs_info", {}).get("commit_id")
    except (metadata.PackageNotFoundError, ValueError):
        return None


def build_info(settings: Settings) -> dict:
    try:
        lib_version = metadata.version("gemini-webapi")
    except metadata.PackageNotFoundError:
        lib_version = None
    return {
        "version": __version__,
        "commit": settings.build_commit or None,
        "branch": settings.build_branch or None,
        "built_at": settings.build_date or None,
        "gemini_webapi": {"version": lib_version, "commit": _library_commit()},
    }


def _branch(settings: Settings) -> str:
    return settings.update_branch or settings.build_branch or "main"


def _release_tuple(version: str | None) -> tuple[int, ...]:
    """``v2.1.1`` / ``2.1.1.post2`` → ``(2, 1, 1)``; a post-release is not a new release."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", version or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def _commits(compare: dict) -> list[dict]:
    out = []
    for c in reversed(compare.get("commits") or []):  # newest first
        out.append(
            {
                "sha": c["sha"][:7],
                "message": (c["commit"]["message"] or "").split("\n", 1)[0][:200],
                "date": c["commit"]["committer"]["date"],
                "url": c.get("html_url"),
            }
        )
    return out[:MAX_COMMITS]


class _GitHub:
    def __init__(self, settings: Settings) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"gemini-webapi-proxy/{__version__}",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        self.http = httpx.AsyncClient(
            base_url=API,
            headers=headers,
            timeout=15,
            proxy=settings.http_proxy or None,
            trust_env=False,
        )

    async def get(self, path: str) -> dict:
        resp = await self.http.get(path)
        if resp.status_code >= 400:
            message = (
                resp.json().get("message", "")
                if resp.headers.get("content-type", "").startswith("application/json")
                else ""
            )
            raise RuntimeError(f"GitHub {resp.status_code} {path}: {message}"[:300])
        return resp.json()

    async def compare(self, repo: str, base: str, head: str) -> dict:
        data = await self.get(f"/repos/{repo}/compare/{base}...{head}")
        return {
            "behind": data.get("ahead_by", 0),
            "commits": _commits(data),
            "url": data.get("html_url"),
        }


async def _proxy(gh: _GitHub, settings: Settings) -> dict:
    repo, branch, commit = settings.update_repo, _branch(settings), settings.build_commit
    out: dict[str, Any] = {"repo": repo, "branch": branch, "commit": commit or None}
    if commit:
        out.update(await gh.compare(repo, commit, branch))
    else:  # run from source: only show what the branch is at
        head = await gh.get(f"/repos/{repo}/commits/{branch}")
        out.update(behind=None, commits=_commits({"commits": [head]}))
    return out


async def _library(gh: _GitHub, settings: Settings, info: dict) -> dict:
    repo, version, commit = settings.library_repo, info["version"], info["commit"]
    out: dict[str, Any] = {"repo": repo, "version": version, "commit": commit}
    try:
        rel = await gh.get(f"/repos/{repo}/releases/latest")
        out["release"] = {
            "tag": rel.get("tag_name"),
            "date": rel.get("published_at"),
            "url": rel.get("html_url"),
        }
        out["release_newer"] = _release_tuple(rel.get("tag_name")) > _release_tuple(version)
    except RuntimeError as exc:
        out["release"], out["release_newer"] = None, False
        out["release_error"] = str(exc)
    if commit:
        out.update(await gh.compare(repo, commit, "HEAD"))
    return out


async def _upstream(gh: _GitHub, settings: Settings) -> dict:
    repo = settings.upstream_repo
    owner = settings.update_repo.split("/", 1)[0]
    out: dict[str, Any] = {"repo": repo}
    out.update(await gh.compare(repo, f"{owner}:{_branch(settings)}", "HEAD"))
    return out


def _new_commits(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} новый коммит"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} новых коммита"
    return f"{n} новых коммитов"


def available(result: dict) -> list[str]:
    """Human summary of what is newer; empty when everything is current."""
    found = []
    proxy, lib, up = (
        result.get("proxy") or {},
        result.get("library") or {},
        result.get("upstream") or {},
    )
    if proxy.get("behind"):
        found.append(f"прокси: {_new_commits(proxy['behind'])} в {proxy['branch']}")
    if lib.get("release_newer"):
        found.append(f"gemini-webapi: релиз {lib['release']['tag']}")
    if lib.get("behind"):
        found.append(f"gemini-webapi: {_new_commits(lib['behind'])} после закреплённого")
    if up.get("behind"):
        found.append(f"исходный прокси: {_new_commits(up['behind'])}, которых нет в нашей ветке")
    return found


def _signature(result: dict) -> str:
    def head(part: str) -> str:
        commits = (result.get(part) or {}).get("commits") or []
        return commits[0]["sha"] if commits and (result.get(part) or {}).get("behind") else ""

    lib = result.get("library") or {}
    tag = (lib.get("release") or {}).get("tag") if lib.get("release_newer") else ""
    return "|".join([head("proxy"), head("library"), tag or "", head("upstream")])


def _path(settings: Settings):
    return settings.data_dir / "updates.json"


def load(settings: Settings) -> None:
    try:
        state.update(json.loads(_path(settings).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def _save(settings: Settings) -> None:
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _path(settings).write_text(
            json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("updates: cannot save %s: %s", _path(settings), exc)


async def check(settings: Settings) -> dict:
    """Ask GitHub now; one check at a time."""
    async with _lock:
        info = build_info(settings)
        gh = _GitHub(settings)
        result: dict[str, Any] = {"checked_at": time.time()}
        try:
            parts = await asyncio.gather(
                _proxy(gh, settings),
                _library(gh, settings, info["gemini_webapi"]),
                _upstream(gh, settings),
                return_exceptions=True,
            )
        finally:
            await gh.http.aclose()
        for name, part in zip(("proxy", "library", "upstream"), parts, strict=True):
            if isinstance(part, BaseException):
                log.warning("updates: %s: %s", name, part)
                result[name] = {"error": str(part)[:300]}
            else:
                result[name] = part
        result["available"] = available(result)
        signature = _signature(result)
        if result["available"] and signature != state.get("notified"):
            events.emit("update", None, "\n".join(result["available"]))
        result["notified"] = signature if result["available"] else ""
        state.clear()
        state.update(result)
        _save(settings)
        return dict(state)


async def _loop(settings: Settings) -> None:
    await asyncio.sleep(FIRST_CHECK_DELAY)
    period = settings.update_check_hours * 3600
    while True:
        if time.time() - state.get("checked_at", 0) >= period - 60:
            try:
                await check(settings)
            except Exception as exc:  # GitHub down must not kill the loop
                log.warning("updates: check failed: %s", exc)
        await asyncio.sleep(min(period, 3600))


def start(settings: Settings) -> None:
    global _task
    load(settings)
    if settings.update_check_hours > 0 and _task is None:
        _task = asyncio.get_running_loop().create_task(_loop(settings))


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
        _task = None
