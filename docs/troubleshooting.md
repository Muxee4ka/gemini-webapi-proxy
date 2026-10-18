# Troubleshooting & Maintenance

## Quick diagnosis

```bash
# 1. Process is listening on 4982?
curl -sS http://localhost:4982/health

# 2. What models does the proxy advertise right now?
curl -sS http://localhost:4982/openai/v1/models | python3 -m json.tool

# 3. Docker status & recent logs
docker compose ps
docker compose logs --tail=100

# 4. End-to-end self-test (real Gemini session required)
bash scripts/e2e-image.sh
```

## Common symptoms

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Failed to connect to localhost:4982` | Proxy not started | `bash scripts/docker-up.sh` or `./start.sh` |
| Docker container can't reach `127.0.0.1:7897` | Inside the container, `127.0.0.1` is the **container itself**, not the host | In `.env`, set `GOP_HTTP_PROXY=http://host.docker.internal:7897`. (Or use `network_mode: host` — already the default in this repo.) |
| 401 / `signed out` / `AuthError` | Cookie expired | Re-login in Safari at [gemini.google.com](https://gemini.google.com), then `.venv/bin/python scripts/sync_runtime_env.py && docker compose restart` |
| 500 when generating several images concurrently | Cookie refresh storm | Set your client's image concurrency to **1–2** and restart the proxy |
| 502 with `Multiple cookies exist with name=NID` / `COMPASS` | Cross-domain cookie duplicates (`.com` vs `.com.hk`) | Already filtered; if it persists, `docker compose restart` |
| 502 with no b64 in the response | Playwright download failed for all five strategies | `playwright install chromium` on the host; for Docker, `docker compose build --no-cache && docker compose up -d` |
| `Upstream refused request: I cannot fulfill…` → HTTP 403 | Gemini's safety/policy filter | This is a *real* answer from Gemini, not a bug. Try a different prompt or model. |
| `Unknown model name: gemini-2.5-pro-image` despite asking for `gemini-3-pro` | **Fixed in 0.1.0** — old image had a routing bug | `docker compose pull && docker compose up -d --force-recreate` |
| `docker ps` shows `unhealthy` but `/health` returns ok | Old healthcheck used `curl`; the image now uses `urllib` | Pull the new image: `docker compose pull && docker compose up -d --force-recreate` |
| Image-generation prompt rejected as "I cannot fulfill" but text-only chat is fine | Specific safety filter hit on the image pipeline | Re-phrase the prompt (no human faces, copyrighted characters, etc.) or use the Flash model which has looser filters |
| HTTP 429 `Gemini usage limit reached: … as soon as your limit resets` | The account's quota is used up (logs show `Account quota EXHAUSTED … Resets: <time>`) | Wait for the reset time from the log. Long agent prompts with tool specs spend quota fast |
| `/models` lists only `gemini-flash-lite`; `Unknown model name: 'gemini-2.5-flash-image'` | The session is logged out (log: `Account status: UNAUTHENTICATED`), a guest session sees only the default model. Usually a restart reused a stale `__Secure-1PSIDTS` | Export fresh cookies; set `GEMINI_COOKIE_PATH` to a persistent dir so rotated cookies survive restarts. If copied cookies keep dying after a few hours, switch to `GOP_COOKIE_SOURCE=cdp` ([cookie-sources.md](cookie-sources.md#cdp-recommended-for-servers)) |
| Video job `failed` with `video_limit_reached` | Daily video limit used up; Gemini drew an image instead of a video | Wait for the reset; do not resubmit in a loop |
| Video job `failed` with `refused` | Gemini refused the prompt | Remove brand, cartoon, celebrity and real people's names; describe the scene and motion |
| `403` with `error code: 1010` from a script behind Cloudflare | Cloudflare blocks the client's signature (e.g. `Python-urllib`) | Send a browser-like `User-Agent` |

## Cookie cache

`gemini-webapi` caches a copy of the session cookies in the temp dir
(`.cached_cookies_*.json`). A **stale** cache causes "browser still
logged in but API 401" — a particularly confusing failure mode.

**Host mode:** `session_bootstrap` clears the cache on startup unless
`GEMINI_WEBAPI_KEEP_COOKIE_CACHE=true`.

**Manual cleanup:**

```bash
# macOS / Linux
rm -f /var/folders/*/*/T/gemini_webapi/.cached_cookies_*.json
rm -f "$(python3 -c 'import tempfile; print(tempfile.gettempdir())')"/gemini_webapi/.cached_cookies_*.json
```

**Docker mode:** the cache lives in the container's tmp dir; restart
the container **and** re-run `scripts/sync_runtime_env.py` to overwrite
it with fresh cookies.

## Host mode vs Docker mode

| | `./start.sh` (venv) | `bash scripts/docker-up.sh` |
|--|---------------------|----------------------------|
| Cookie source | Reads your browser **every request** (auto-refresh on `AuthError`) | Reads `data/runtime.env` (manual sync) |
| HTTP proxy | `http://127.0.0.1:7897` (your machine's loopback) | `http://host.docker.internal:7897` (host loopback **from inside the container**) |
| Long-running | Terminal must stay open | `restart: unless-stopped` survives reboots |
| Recommended for | Development, debugging, live testing | Daily Studio usage |

The Docker container uses `network_mode: host`, so it shares the host's
loopback — there is no port mapping, and `127.0.0.1` *inside* the
container is the host's `127.0.0.1`. If you disable `network_mode:
host` (don't, but if you do), you'll need to either bind-mount the
proxy port or set `extra_hosts: ["host.docker.internal:host-gateway"]`.

## Development self-test

```bash
.venv/bin/pytest tests/ -q
bash scripts/e2e-image.sh
```

The e2e script calls the local proxy and verifies the response is
base64-decodable PNG bytes. It requires a real logged-in session.
