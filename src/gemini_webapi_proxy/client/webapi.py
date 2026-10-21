"""Adapter around the (reverse-engineered) gemini-webapi ``GeminiClient``."""

from __future__ import annotations

import logging
import os
from typing import Any

from gemini_webapi import GeminiClient as _RealClient
from gemini_webapi.exceptions import APIError
from gemini_webapi.utils.decorators import running

from gemini_webapi_proxy.client.base import (
    BaseGeminiClient,
    GenerationResult,
    ModelDescriptor,
)
from gemini_webapi_proxy.errors import UpstreamRefusalError
from gemini_webapi_proxy.utils.upstream_refusal import looks_like_upstream_refusal


def _unwrap_decorated(func: Any) -> Any:
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func


logger = logging.getLogger(__name__)

_RAW_GENERATE = _unwrap_decorated(_RealClient._generate)
_MAX_WEBAPI_RETRY = max(0, int(os.getenv("GOP_WEBAPI_MAX_RETRY", "1")))
_GENERATE_RETRYING = running(retry=_MAX_WEBAPI_RETRY)(_RAW_GENERATE)
_GENERATE_ONCE = running(retry=0)(_RAW_GENERATE)

# Gemini Web may close a finished turn with BardErrorInfo 1096 after the whole answer
# (the web app gets it too, e.g. on guest sessions); the library treats every error
# code as fatal and drops the answer.
_TRAILING_ERROR = "Unknown API error code: 1096"


class _LowRetryGeminiClient(_RealClient):
    """Gemini Web client with fewer automatic retries than upstream default (5).

    ``current_retry=0`` (passed through ``generate_content``) disables retries for one
    call. gemini-webapi <= 2.1.1 read it in ``@running``; newer versions do not, so it
    is handled here.
    """

    async def _generate(self, *args: Any, current_retry: int | None = None, **kwargs: Any):
        generate = _GENERATE_ONCE if current_retry == 0 else _GENERATE_RETRYING
        last = None
        try:
            async for item in generate(self, *args, **kwargs):
                last = item
                yield item
        except APIError as exc:
            if _TRAILING_ERROR not in str(exc) or not (getattr(last, "text", "") or "").strip():
                raise
            logger.info("Ignoring Gemini error 1096 after a complete answer")


class WebAPIClient(BaseGeminiClient):
    """Thin adapter over :class:`gemini_webapi.GeminiClient`.

    The underlying client is exposed as :attr:`native` so that downstream
    code which needs a feature not yet covered by :class:`BaseGeminiClient`
    (e.g. the full ``client_ref`` for image downloads) can still reach it.
    """

    def __init__(self, psid: str, psidts: str, *, proxy: str | None = None) -> None:
        self._client = _LowRetryGeminiClient(psid, psidts, proxy=proxy)

    @property
    def native(self) -> _LowRetryGeminiClient:
        """The underlying ``gemini_webapi.GeminiClient`` instance."""
        return self._client

    @property
    def access_token(self) -> str | None:
        return getattr(self._client, "access_token", None)

    @property
    def account_status(self) -> Any:
        return getattr(self._client, "account_status", None)

    @property
    def cookies(self) -> Any:
        return self._client.cookies

    @cookies.setter
    def cookies(self, value: Any) -> None:
        self._client.cookies = value

    async def init(self, *, timeout: float, auto_refresh: bool) -> None:
        await self._client.init(timeout=timeout, auto_refresh=auto_refresh)

    async def close(self) -> None:
        await self._client.close()

    def list_models(self) -> list[ModelDescriptor]:
        models = self._client.list_models() or []
        out: list[ModelDescriptor] = []
        for m in models:
            if not getattr(m, "is_available", False):
                continue
            name = (getattr(m, "model_name", "") or "").strip()
            if not name:
                continue
            out.append(
                ModelDescriptor(
                    name=name,
                    display_name=getattr(m, "display_name", "") or name,
                    description=getattr(m, "description", "") or "",
                )
            )
        return out

    async def generate_content(
        self,
        prompt: str,
        *,
        files: list[Any] | None,
        model: Any,
    ) -> GenerationResult:
        output = await self._client.generate_content(prompt, files=files or None, model=model)
        text = (getattr(output, "text", "") or "").strip()
        images = list(getattr(output, "images", None) or [])
        if text and not images and looks_like_upstream_refusal(text):
            raise UpstreamRefusalError(text)
        return GenerationResult(text=text, images=images)
