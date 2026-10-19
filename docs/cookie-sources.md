# Cookie Sources

The proxy can read Gemini Web cookies from four sources. Pick one with
`GOP_COOKIE_SOURCE`.

| Source | `GOP_COOKIE_SOURCE` | Best for | Cookie refresh |
|--------|---------------------|----------|----------------|
| Desktop browser | `browser` | Local development on macOS / desktop Linux | Automatic on every request |
| Environment variables | `env` | Docker, headless servers, CI | Manual (re-export + restart) |
| Netscape cookie file | `file` | Headless servers without browser access | Manual (re-export + reload) |
| Chrome over DevTools | `cdp` | Servers: sign in once in a browser on the server | Automatic (the proxy rotates); re-read after a re-login |

**Why copied cookies die on a server.** `env` and `file` give the proxy a copy of a
session that lives in your desktop browser. The proxy rotates `__Secure-1PSIDTS` on its
own, but sessions created in desktop Chrome are increasingly device-bound
(`__Secure-1PSIDRTS` cookies): only that browser can keep extending them. In practice a
copied jar works for a few hours and then turns into a logged-out guest session
(`Account status: UNAUTHENTICATED`, `/models` shows only `gemini-flash-lite`). The
upstream project avoids this by running next to a real browser (`browser`). On a
server, `cdp` creates the session on the server itself.

**Never use one session from two clients at once.** Each client gets its own rotated
`__Secure-1PSIDTS`, the session forks, and Google revokes it, the browser's copy
included. This is why `cdp` takes the session away from the browser instead of sharing it.

## `browser` (default on desktop)

Uses [`browser-cookie3`](https://github.com/borisbabic/browser_cookie3)
to read `google.com` cookies from a real browser. Order of preference:

* macOS: Safari, Chrome, Edge, Brave, Chromium
* Linux / Windows: Chrome, Edge, Brave, Chromium, Safari

Pick a specific browser with `GOP_BROWSER=safari|chrome|edge|brave|chromium`.

```bash
# macOS — first time only
pip install "gemini-webapi-proxy[browser-cookie]"
.venv/bin/playwright install chromium   # only if you'll generate images
./start.sh
```

The first request to the proxy will read the cookies, call
`gemini-webapi.init()`, and on subsequent requests the client stays in
memory. If the cookies go stale, the proxy auto-invalidates its client
and the next request re-reads the browser.

## `env` (recommended for Docker)

Set the cookie values directly:

```bash
export GOP_COOKIE_SOURCE=env
export GOP_GEMINI_1PSID='paste from DevTools'
export GOP_GEMINI_1PSIDTS='paste from DevTools'
export GOP_HTTP_PROXY='http://host.docker.internal:7897'
./start.sh
```

To obtain the values:

1. Open [gemini.google.com](https://gemini.google.com) in a logged-in
   browser tab.
2. DevTools → Application → Cookies → `https://gemini.google.com`.
3. Copy the values of `__Secure-1PSID` and `__Secure-1PSIDTS`.

Or use the helper script (macOS hosts only):

```bash
python scripts/sync_runtime_env.py --browser safari
# writes data/runtime.env (chmod 600) with GOP_GEMINI_1PSID/PSIDTS/PROXY
```

## `file`

Useful for headless Linux servers that don't have a desktop browser and
where you don't want to hand-edit env files.

```bash
# 1. On any machine that has a browser session:
#    install "Get cookies.txt LOCALLY" extension,
#    export cookies for google.com → save as cookies.txt
# 2. SCP / copy cookies.txt to the server
# 3. Set:
export GOP_COOKIE_SOURCE=file
export GOP_COOKIE_FILE=/etc/gemini-webapi-proxy/cookies.txt
./start.sh
```

Expected file format (Netscape, the same one `curl` uses):

```
# Netscape HTTP Cookie File
.google.com	TRUE	/	TRUE	9999999999	__Secure-1PSID	abc123
.google.com	TRUE	/	TRUE	9999999999	__Secure-1PSIDTS	xyz789
```

The `HttpOnly_` prefix is tolerated (some exporters add it).

## `cdp` (recommended for servers)

A Chromium runs next to the proxy with its own profile, and you sign in to Google in
it once by hand. The session is created on the server, so it is not bound to your
desktop.

How it works:

* on client init (startup, or a logged-out session being retried) the proxy reads the
  browser's cookie jar over the DevTools protocol, without loading any page;
* right after that every Google tab in the browser is switched to `about:blank`, so the
  browser stops using the session; from then on the proxy owns it and gemini-webapi
  rotates `__Secure-1PSIDTS`, cached in `GEMINI_COOKIE_PATH` (keep it on a volume);
* if the session gets logged out, the proxy retries at most once a minute (on
  requests), picking up a fresh sign-in by itself.

Setup with the bundled container ([`integrations/cdp-browser`](../integrations/cdp-browser)):
Chromium on a virtual display (Google sign-in treats headless Chrome as a bot),
profile in `./profile`, DevTools on `127.0.0.1:9223`, starts on `about:blank`,
about 300-600 MB RAM.

```bash
cd integrations/cdp-browser
mkdir -p profile && sudo chown 1000:1000 profile   # the browser runs as uid 1000
docker compose up -d --build
```

Sign in through an SSH tunnel from your desktop:

1. `ssh -N -L 9333:127.0.0.1:9223 user@server`
2. In desktop Chrome open `chrome://inspect/#devices` → **Configure…** → add
   `localhost:9333`.
3. Under **Remote Target** click **inspect** on the tab, open
   `https://gemini.google.com/app` in it and sign in inside the screencast
   (password, 2FA).
4. Send any request to the proxy (or wait for the next one): the tab jumps to
   `about:blank`, which means the proxy took the session.

Then point the proxy at it (both containers on the host network):

```bash
GOP_COOKIE_SOURCE=cdp
GOP_CDP_URL=http://127.0.0.1:9223
GEMINI_COOKIE_PATH=/app/data/cookie-cache
```

Don't open Gemini in that browser afterwards and don't copy the cookies anywhere
else: that would fork the session again. Expect a "new sign-in from Linux" mail
from Google.
