"""Optional API-key authentication.

The proxy is designed for local single-user use and verifies no token by
default.  Set ``GOP_API_KEY`` (non-empty) to require a Bearer token on
every non-health endpoint.  When unset, this module is a pass-through.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status
from fastapi.security.utils import get_authorization_scheme_param
from starlette.requests import HTTPConnection

from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.keys import MASTER, key_store
from gemini_webapi_proxy.stats import note


def require_api_key_if_configured(request: Request) -> None:
    """Reject requests without a matching Bearer token when a key is configured.

    Accepted: ``GOP_API_KEY`` (the master key) or an enabled client key from the
    admin UI.  The key's name goes into the request journal.
    """
    settings = get_settings()
    expected = settings.api_key
    clients = key_store(settings.data_dir)
    if not expected and not clients:
        return

    auth = request.headers.get("Authorization") or ""
    scheme, param = get_authorization_scheme_param(auth)
    if scheme.lower() != "bearer" or not param:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header (expected: Bearer <GOP_API_KEY>)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if expected and hmac.compare_digest(param.encode(), expected.encode()):
        note(key=MASTER)
        return
    client = clients.match(param)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    note(key=client.name)


# ---- Admin UI session --------------------------------------------------------
# The UI signs in with GOP_ADMIN_PASSWORD (or the API key) and gets a signed cookie;
# scripts keep using the Bearer API key.

SESSION_COOKIE = "gop_admin"
SESSION_SECONDS = 7 * 24 * 3600


def _admin_password() -> str | None:
    settings = get_settings()
    return settings.admin_password or settings.api_key


def _sign(password: str, expires: int) -> str:
    key = hashlib.sha256(f"gop-admin:{password}".encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def check_admin_password(candidate: str) -> bool:
    password = _admin_password()
    return bool(password) and hmac.compare_digest(candidate.encode(), password.encode())  # type: ignore[union-attr]


def new_session_cookie() -> tuple[str, int]:
    password = _admin_password() or ""
    expires = int(time.time()) + SESSION_SECONDS
    return f"{expires}.{_sign(password, expires)}", SESSION_SECONDS


def session_valid(cookie: str | None) -> bool:
    password = _admin_password()
    if not password:
        return True  # no password configured: the proxy is open, so is the UI
    if not cookie or "." not in cookie:
        return False
    expires, _, sig = cookie.partition(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(sig, _sign(password, int(expires)))


def _bearer_ok(request: HTTPConnection) -> bool:
    expected = get_settings().api_key
    scheme, param = get_authorization_scheme_param(request.headers.get("Authorization") or "")
    return (
        bool(expected)
        and scheme.lower() == "bearer"
        and hmac.compare_digest(
            param.encode(),
            expected.encode(),  # type: ignore[union-attr]
        )
    )


def require_admin(request: HTTPConnection) -> None:
    """Bearer API key (scripts) or a signed-in UI session."""
    if _bearer_ok(request) or session_valid(request.cookies.get(SESSION_COOKIE)):
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to the admin UI")
