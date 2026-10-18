# Configuration

All runtime configuration is read from environment variables. The
recommended form is **`GOP_`-prefixed**; a handful of unprefixed names
(`PORT`, `USE_BROWSER_COOKIES`, `BROWSER_COOKIE`, `GEMINI_*`,
`PROBE_ON_START`) are accepted as legacy aliases for users migrating
from earlier versions.

Variables are evaluated once on first call to `get_settings()` and
cached for the process lifetime. Set them before launching the server.

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `GOP_HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` to refuse external connections. |
| `GOP_PORT` | `4982` | Bind port. |
| `GOP_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`. |
| `GOP_LOG_FORMAT` | `console` | `console` (human-readable) or `json` (one line per record, for container log scrapers). |
| `GOP_API_KEY` | _(empty)_ | When non-empty, every request must include `Authorization: Bearer <key>`. When empty, auth is bypassed. |
| `GOP_CORS_ORIGINS` | `*` | Comma-separated list, or `*` for any. |

## Cookie source

| Variable | Default | Description |
|----------|---------|-------------|
| `GOP_COOKIE_SOURCE` | `browser` | One of `browser` / `env` / `file` / `cdp`. |
| `GOP_CDP_URL` | `http://127.0.0.1:9223` | `cdp` only: DevTools endpoint of the browser that owns the session. |
| `GOP_CDP_REFRESH_SECONDS` | `600` | `cdp` only: how often the browser's cookies are re-read (the Gemini tab is reloaded first). |
| `GOP_BROWSER` | `auto` | When `cookie_source=browser`, which browser to read from. `auto` picks the first installed (Safari first on macOS, Chrome first on Linux). |
| `GOP_COOKIE_FILE` | _(empty)_ | When `cookie_source=file`, path to a Netscape-format `cookies.txt`. |
| `GOP_GEMINI_1PSID` | _(empty)_ | `__Secure-1PSID` value (env / file modes). |
| `GOP_GEMINI_1PSIDTS` | _(empty)_ | `__Secure-1PSIDTS` value. |
| `GOP_GEMINI_COOKIES_RAW` | _(empty)_ | `k1=v1; k2=v2` form for extra cookies. |

See [cookie-sources.md](cookie-sources.md) for the pros/cons of each
source and a worked example of exporting a `cookies.txt`.

## Network

| Variable | Default | Description |
|----------|---------|-------------|
| `GOP_HTTP_PROXY` | _(empty)_ | Local HTTP proxy (e.g. `http://127.0.0.1:7897`). |
| `GOP_CDN_DIRECT` | `true` | When true, Google CDN domains (`googleusercontent.com`) bypass the proxy to avoid MITM 403 errors. |

## Timeouts

| Variable | Default | Description |
|----------|---------|-------------|
| `GOP_CHAT_TIMEOUT` | `180` | Seconds for a single chat completion. |
| `GOP_IMAGE_TIMEOUT` | `300` | Seconds for a single image generation + download. |
| `GOP_INIT_TIMEOUT` | `120` | Seconds for the first-time Gemini client init. |
| `GOP_VIDEO_TIMEOUT` | `900` | Seconds for one video job: generation plus download. The job runs in the background, so this does not hold an HTTP request open. |

## Behaviour

| Variable | Default | Description |
|----------|---------|-------------|
| `GOP_PROBE_ON_START` | `false` | Probe every model at startup. Slow; turn on only when debugging. |
| `GOP_PROBE_MODELS` | `true` | Allow probing via `POST /admin/probe-models`. |
| `GOP_ENABLE_STREAM` | `false` | Unused: `stream=true` is always accepted (the answer is generated in full, then sent as SSE). |
| `GOP_WEBAPI_MAX_RETRY` | `1` | Maximum automatic retries for `gemini-webapi`'s RPC layer. Default 1 instead of upstream's 5 so the proxy can fail fast and surface real errors to the client. |

## Data and storage

| Variable | Default | Description |
|----------|---------|-------------|
| `GOP_DATA_DIR` | `./data` | Where the model registry file lives. |
| `GOP_REGISTRY_FILE` | `model_registry.json` | File name within `GOP_DATA_DIR`. |
| `GOP_VIDEO_KEEP_HOURS` | `24` | Finished videos (`GOP_DATA_DIR/videos/*.mp4`) and their jobs are dropped after this many hours. |
| `GEMINI_COOKIE_PATH` | _(temp dir)_ | Read by `gemini-webapi`, not by the proxy: where rotated cookies are cached. Not needed with `cdp`: the cache is cleared before each client init, the browser's jar is always fresher. For `env` / `file`, point it into a persistent volume (the compose file uses `/app/data/cookie-cache`). Otherwise a restart can fall back to the original, already rotated `__Secure-1PSIDTS`, the session turns into a logged-out guest and only `gemini-flash-lite` is listed. |

## Downloader chain

| Variable | Default | Description |
|----------|---------|-------------|
| `GOP_DOWNLOADER_CHAIN` | `playwright-rpc,playwright-preview,httpx-rpc,httpx-preview,library-save` | Ordered list of strategies for turning a `GeneratedImage` into raw bytes. The first strategy to return non-empty bytes wins. |
| `GOP_DOWNLOADER_TIMEOUT` | `120` | Per-strategy timeout in seconds. |

See [downloaders.md](downloaders.md) for what each strategy does and
how to add your own.

## Legacy aliases

These unprefixed names are translated to the `GOP_` form on startup;
they are kept so existing `.env` files from older versions keep working.
**New code should always use the `GOP_` form.**

| Legacy | `GOP_` form | Translation |
|--------|-------------|-------------|
| `PORT` | `GOP_PORT` | passthrough |
| `HOST` | `GOP_HOST` | passthrough |
| `HTTP_PROXY` / `HTTPS_PROXY` | `GOP_HTTP_PROXY` | passthrough |
| `USE_BROWSER_COOKIES` | `GOP_COOKIE_SOURCE` | `true` → `browser`, `false` → `env` |
| `BROWSER_COOKIE` | `GOP_BROWSER` | passthrough |
| `GEMINI_1PSID` | `GOP_GEMINI_1PSID` | passthrough |
| `GEMINI_1PSIDTS` | `GOP_GEMINI_1PSIDTS` | passthrough |
| `GEMINI_COOKIES` | `GOP_GEMINI_COOKIES_RAW` | passthrough |
| `PROBE_ON_START` | `GOP_PROBE_ON_START` | passthrough |
| `PROBE_MODELS` | `GOP_PROBE_MODELS` | passthrough |
