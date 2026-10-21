"""Named API keys for the proxy's clients, kept in ``<data_dir>/keys.json``.

``GOP_API_KEY`` stays the master key; these are extra keys, one per client
(OpenClaw, a bot, …), so the request journal shows who spent what and a single
client can be cut off.  Only a SHA-256 of each key is stored: the key itself is
shown once, when it is created.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

KEYS_FILE = "keys.json"
MASTER = "master"


@dataclass
class ClientKey:
    id: str
    name: str
    prefix: str  # first characters, to recognise a key in a client's config
    sha256: str
    created_at: float
    enabled: bool = True

    def public(self) -> dict:
        out = asdict(self)
        out.pop("sha256")
        return out


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._keys: dict[str, ClientKey] = {}
        if path.is_file():
            raw = json.loads(path.read_text())
            self._keys = {k["id"]: ClientKey(**k) for k in raw.get("keys", [])}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"keys": [asdict(k) for k in self._keys.values()]}, f, indent=2)
        tmp.replace(self.path)

    def all(self) -> list[ClientKey]:
        return sorted(self._keys.values(), key=lambda k: k.created_at)

    def __bool__(self) -> bool:
        return bool(self._keys)

    def create(self, name: str) -> tuple[ClientKey, str]:
        secret = "gop-" + secrets.token_urlsafe(32)
        key = ClientKey(
            id=secrets.token_hex(4),
            name=name.strip() or "client",
            prefix=secret[:10],
            sha256=_digest(secret),
            created_at=time.time(),
        )
        self._keys[key.id] = key
        self._save()
        return key, secret

    def update(self, key_id: str, **changes) -> ClientKey:
        key = self._keys[key_id]
        for name, value in changes.items():
            setattr(key, name, value)
        self._save()
        return key

    def remove(self, key_id: str) -> None:
        self._keys.pop(key_id)
        self._save()

    def match(self, candidate: str) -> ClientKey | None:
        digest = _digest(candidate)
        for key in self._keys.values():
            if key.enabled and hmac.compare_digest(digest, key.sha256):
                return key
        return None


_store: KeyStore | None = None


def key_store(data_dir: Path) -> KeyStore:
    global _store
    path = data_dir / KEYS_FILE
    if _store is None or _store.path != path:
        _store = KeyStore(path)
    return _store
