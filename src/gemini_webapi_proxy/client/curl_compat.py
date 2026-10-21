"""Let gemini-webapi follow Google's redirects through a local proxy.

gemini-webapi opens its curl_cffi sessions with ``allow_redirects=CurlFollow.SAFE``:
redirects to internal addresses are refused.  Behind a proxy, curl checks the
address it is connected to, which is the proxy, so with an account proxy on
``127.0.0.1`` (the local egress, see docs/accounts.md) every Google redirect fails
with ``Redirect to internal IP 127.0.0.1 rejected (SSRF protection)``.

Only sessions that go through a proxy on a loopback or private address get plain
redirects; the targets are still Google's own, chosen by Google.  Every other
session keeps the library's SAFE mode.
"""

from __future__ import annotations

import importlib
import ipaddress
from typing import Any
from urllib.parse import urlsplit

from curl_cffi import CurlFollow
from curl_cffi.requests import AsyncSession


def local_proxy(proxy: str | None) -> bool:
    if not proxy:
        return False
    host = urlsplit(proxy if "://" in proxy else f"http://{proxy}").hostname or ""
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class LocalProxySession(AsyncSession):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("allow_redirects") == CurlFollow.SAFE and local_proxy(kwargs.get("proxy")):
            kwargs["allow_redirects"] = True
        super().__init__(*args, **kwargs)


_MODULES = (
    # get_access_token is also the name of the function the package re-exports,
    # so the modules are imported by their full names.
    "gemini_webapi.utils.get_access_token",
    "gemini_webapi.types.image",
    "gemini_webapi.types.video",
)


def install() -> None:
    """Swap the session class in the gemini-webapi modules that open one."""
    for name in _MODULES:
        module = importlib.import_module(name)
        if module.AsyncSession in (AsyncSession, LocalProxySession):
            module.AsyncSession = LocalProxySession
        else:  # a new library version changed how sessions are made: say so
            raise RuntimeError(f"{name}.AsyncSession is not curl_cffi's: update curl_compat")
