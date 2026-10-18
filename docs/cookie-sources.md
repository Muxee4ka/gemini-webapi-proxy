# Cookie Sources

The proxy can read Gemini Web cookies from four sources. Pick one with
`GOP_COOKIE_SOURCE`.

| Source | `GOP_COOKIE_SOURCE` | Best for | Cookie refresh |
|--------|---------------------|----------|----------------|
| Desktop browser | `browser` | Local development on macOS / desktop Linux | Automatic on every request |
| Environment variables | `env` | Docker, headless servers, CI | Manual (re-export + restart) |
| Netscape cookie file | `file` | Headless servers without browser access | Manual (re-export + reload) |
| Chrome over DevTools | `cdp` | Servers: a browser on the server owns the session | Automatic, every `GOP_CDP_REFRESH_SECONDS` |

**Why copied cookies die on a server.** `env` and `file` give the proxy a copy of a
session that lives in your desktop browser. The proxy rotates `__Secure-1PSIDTS` on its
own, but sessions created in desktop Chrome are increasingly device-bound
(`__Secure-1PSIDRTS` cookies): only that browser can keep extending them. In practice a
copied jar works for a few hours and then turns into a logged-out guest session
(`Account status: UNAUTHENTICATED`, `/models` shows only `gemini-flash-lite`). The
upstream project avoids this by running next to a real browser (`browser`); `cdp` does
the same on a server.

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

A Chromium runs next to the proxy with its own profile, signed in to Google once by
hand. The browser keeps the session alive; the proxy reads its cookies over the
DevTools protocol and never rotates them itself (two rotation chains would fork the
session).

How it works:

* every `GOP_CDP_REFRESH_SECONDS` (600) the next request reloads the Gemini tab in the
  browser, reads its cookies and swaps in a new Gemini client; the old one is closed
  a few minutes later, after in-flight requests finish;
* if the browser is unreachable, the proxy keeps the current client;
* an auth error drops the client, and the next request re-reads the browser.

Setup with the bundled container ([`integrations/cdp-browser`](../integrations/cdp-browser)):
Chromium on a virtual display (Google sign-in treats headless Chrome as a bot),
profile in `./profile`, DevTools on `127.0.0.1:9223`, about 600 MB RAM.

```bash
cd integrations/cdp-browser
mkdir -p profile && sudo chown 1000:1000 profile   # the browser runs as uid 1000
docker compose up -d --build
```

Sign in once through an SSH tunnel from your desktop:

1. `ssh -N -L 9333:127.0.0.1:9223 user@server`
2. In desktop Chrome open `chrome://inspect/#devices` → **Configure…** → add
   `localhost:9333`.
3. Under **Remote Target** click **inspect** on the *Google Gemini* tab and sign in
   inside the screencast (password, 2FA).

Then point the proxy at it (both containers on the host network):

```bash
GOP_COOKIE_SOURCE=cdp
GOP_CDP_URL=http://127.0.0.1:9223
```

Use a session that exists only in this browser: don't copy the same cookies into
another proxy, and expect a "new sign-in from Linux" mail from Google.
