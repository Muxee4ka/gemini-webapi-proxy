"""Google accounts the proxy can use, kept in ``<data_dir>/accounts.json``.

An account either carries the cookie jar it was signed in with (``stored``: taken
from the sign-in browser or pasted in the admin UI) or reads the configured
``GOP_COOKIE_SOURCE`` like a single-account proxy does (``settings``).  The jar is
only the starting point: once a client is up, gemini-webapi rotates the session and
keeps the current cookies in ``GEMINI_COOKIE_PATH`` (one file per ``__Secure-1PSID``).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

ACCOUNTS_FILE = "accounts.json"


@dataclass
class Account:
    id: str
    name: str
    source: Literal["stored", "settings"] = "stored"
    enabled: bool = True
    priority: int = 0  # lower is tried first
    cookies: dict[str, str] = field(default_factory=dict)
    email: str | None = None
    signed_in_at: float | None = None
    # Outbound proxy towards Google for this account (http://, socks5://); None: direct.
    proxy: str | None = None
    egress: dict | None = None  # last proxy check: ip, country, city, org, checked_at
    proxy_set_at: float | None = None  # a sign-in before this came from another address

    def public(self) -> dict:
        """The account without its cookies and with any proxy password masked."""
        out = asdict(self)
        out.pop("cookies")
        out["proxy"] = mask_proxy(self.proxy)
        if self.egress:
            out["egress"] = {**self.egress, "proxy": mask_proxy(self.egress.get("proxy"))}
        return out


def mask_proxy(proxy: str | None) -> str | None:
    if not proxy:
        return proxy
    parts = urlsplit(proxy)
    if not parts.password:
        return proxy
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return f"{parts.scheme}://{parts.username}:***@{host}"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "account"


class AccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._accounts: dict[str, Account] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            # First start: the proxy keeps working off GOP_COOKIE_SOURCE.
            self._accounts = {"default": Account(id="default", name="default", source="settings")}
            return
        raw = json.loads(self.path.read_text())
        self._accounts = {a["id"]: Account(**a) for a in raw.get("accounts", [])}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"accounts": [asdict(a) for a in self._accounts.values()]}
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def all(self) -> list[Account]:
        return sorted(self._accounts.values(), key=lambda a: (a.priority, a.id))

    def get(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    def add(self, name: str, *, priority: int | None = None) -> Account:
        base = slugify(name)
        account_id, n = base, 2
        while account_id in self._accounts:
            account_id, n = f"{base}-{n}", n + 1
        if priority is None:
            priority = max((a.priority for a in self._accounts.values()), default=-1) + 1
        account = Account(id=account_id, name=name.strip() or account_id, priority=priority)
        self._accounts[account_id] = account
        self.save()
        return account

    def update(self, account_id: str, **changes) -> Account:
        account = self._accounts[account_id]
        for key, value in changes.items():
            setattr(account, key, value)
        self.save()
        return account

    def set_cookies(self, account_id: str, cookies: dict[str, str], email: str | None) -> Account:
        return self.update(
            account_id,
            source="stored",
            cookies=dict(cookies),
            email=email,
            signed_in_at=time.time(),
        )

    def remove(self, account_id: str) -> None:
        self._accounts.pop(account_id)
        self.save()
