"""Request journal for the admin UI: who called what, on which account, how it went.

Each OpenAI-style request leaves one record (no prompt or answer text, only sizes):
time, endpoint, model, client key, account(s) tried, HTTP status, duration, error.
Records sit in memory (the last ``MAX_RECORDS``) and in ``<data_dir>/requests.jsonl``
so a restart keeps the history.

Code on the request path adds fields with :func:`note`; the middleware in ``app.py``
opens the record and writes it when the response is ready.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from collections import Counter, deque
from contextvars import ContextVar
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_RECORDS = 5000
REQUESTS_FILE = "requests.jsonl"

current: ContextVar[dict[str, Any] | None] = ContextVar("gop_request", default=None)


def note(**fields: Any) -> None:
    """Add fields to the record of the request being served (no-op outside one)."""
    record = current.get()
    if record is not None:
        record.update({k: v for k, v in fields.items() if v is not None})


def note_account(account_id: str) -> None:
    record = current.get()
    if record is not None:
        record.setdefault("tried", []).append(account_id)
        record["account"] = account_id


class RequestLog:
    def __init__(self) -> None:
        self.records: deque[dict[str, Any]] = deque(maxlen=MAX_RECORDS)
        self.path: Path | None = None
        self._lines = 0

    def open(self, data_dir: Path) -> None:
        path = data_dir / REQUESTS_FILE
        if self.path == path:
            return
        self.path = path
        self.records.clear()
        self._lines = 0
        if path.is_file():
            with path.open() as f:
                for line in f:
                    self._lines += 1
                    try:
                        self.records.append(json.loads(line))
                    except ValueError:
                        continue
            if self._lines > 2 * MAX_RECORDS:
                self._compact()

    def _compact(self) -> None:
        assert self.path is not None
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for record in self.records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp.replace(self.path)
        self._lines = len(self.records)

    def add(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._lines += 1
            if self._lines > 2 * MAX_RECORDS:
                self._compact()
        except OSError as exc:
            log.warning("request log: %s", exc)

    def since(self, seconds: float) -> list[dict[str, Any]]:
        cutoff = time.time() - seconds
        return [r for r in self.records if r.get("ts", 0) >= cutoff]

    def recent(self, limit: int, **filters: str | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for record in reversed(self.records):
            if _matches(record, filters):
                out.append(record)
                if len(out) >= limit:
                    break
        return out


def _ok(record: dict[str, Any]) -> bool:
    return int(record.get("status") or 0) < 400


def _matches(record: dict[str, Any], filters: dict[str, str | None]) -> bool:
    for key, want in filters.items():
        if not want:
            continue
        if key == "outcome":
            if (want == "ok") != _ok(record):
                return False
        elif str(record.get(key) or "") != want:
            return False
    return True


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1]


def summary(records: list[dict[str, Any]], *, hours: int, now: float | None = None) -> dict:
    """Totals and per-hour / per-model / per-account / per-key breakdowns."""
    now = now or time.time()
    start_hour = int(now // 3600) - hours + 1
    hourly = {h: {"ok": 0, "error": 0} for h in range(start_hour, start_hour + hours)}
    durations: list[float] = []
    by: dict[str, Counter] = {k: Counter() for k in ("model", "account", "key", "endpoint")}
    errors_by: dict[str, Counter] = {k: Counter() for k in by}
    ok = err = limited = 0
    for record in records:
        good = _ok(record)
        ok, err = ok + good, err + (not good)
        limited += int(record.get("status") or 0) == 429
        bucket = hourly.get(int(record.get("ts", 0) // 3600))
        if bucket is not None:
            bucket["ok" if good else "error"] += 1
        if good and record.get("ms") is not None:
            durations.append(float(record["ms"]))
        for key, counter in by.items():
            value = record.get(key) or "—"
            counter[value] += 1
            if not good:
                errors_by[key][value] += 1

    def table(key: str) -> list[dict[str, Any]]:
        return [
            {"name": name, "requests": n, "errors": errors_by[key][name]}
            for name, n in by[key].most_common()
        ]

    return {
        "hours": hours,
        "totals": {
            "requests": ok + err,
            "ok": ok,
            "errors": err,
            "limited": limited,
            "p50_ms": _percentile(durations, 50),
            "p95_ms": _percentile(durations, 95),
        },
        "hourly": [{"ts": h * 3600, **v} for h, v in sorted(hourly.items())],
        "models": table("model"),
        "accounts": table("account"),
        "keys": table("key"),
        "endpoints": table("endpoint"),
    }


journal = RequestLog()
