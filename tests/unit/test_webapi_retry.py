"""Unit tests for _LowRetryGeminiClient: per-call retry opt-out and trailing error 1096."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from gemini_webapi.exceptions import APIError

from gemini_webapi_proxy.client import webapi
from gemini_webapi_proxy.client.webapi import WebAPIClient

_ERR_1096 = "Failed to generate contents (stream). Unknown API error code: 1096. Temporary issue."


def _fake_generate(seen: dict, name: str, texts: list[str], error: str | None = None):
    async def generate(client, *args, **kwargs):
        seen["used"] = name
        seen["kwargs"] = kwargs
        for text in texts:
            yield SimpleNamespace(text=text)
        if error:
            raise APIError(error)

    return generate


def _patch(monkeypatch, texts: list[str], error: str | None = None) -> dict:
    seen: dict = {}
    monkeypatch.setattr(
        webapi, "_GENERATE_RETRYING", _fake_generate(seen, "retrying", texts, error)
    )
    monkeypatch.setattr(webapi, "_GENERATE_ONCE", _fake_generate(seen, "once", texts, error))
    return seen


async def _collect(**kwargs) -> list[str]:
    client = WebAPIClient("psid", "psidts").native
    return [item.text async for item in client._generate(prompt="hi", **kwargs)]


@pytest.mark.asyncio
async def test_trailing_1096_after_answer_is_ignored(monkeypatch) -> None:
    _patch(monkeypatch, ["Hi", "Hi!"], _ERR_1096)
    assert await _collect() == ["Hi", "Hi!"]


@pytest.mark.asyncio
async def test_1096_without_answer_is_raised(monkeypatch) -> None:
    _patch(monkeypatch, [""], _ERR_1096)
    with pytest.raises(APIError, match="1096"):
        await _collect()


@pytest.mark.asyncio
async def test_other_error_after_answer_is_raised(monkeypatch) -> None:
    _patch(monkeypatch, ["Hi"], "Unknown API error code: 1037.")
    with pytest.raises(APIError, match="1037"):
        await _collect()


@pytest.mark.asyncio
async def test_current_retry_zero_disables_retries(monkeypatch) -> None:
    seen = _patch(monkeypatch, ["ok"])
    assert await _collect(current_retry=0) == ["ok"]
    assert seen["used"] == "once"
    assert "current_retry" not in seen["kwargs"]


@pytest.mark.asyncio
async def test_default_call_uses_retries(monkeypatch) -> None:
    seen = _patch(monkeypatch, ["ok"])
    await _collect()
    assert seen["used"] == "retrying"
