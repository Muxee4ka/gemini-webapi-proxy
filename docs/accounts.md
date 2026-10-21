# Accounts and the admin UI

The proxy can hold several Google accounts and switch between them by itself. The
admin UI at **`/admin/ui`** shows each account's state and limits, and signs accounts
in (again) through a browser running on the server.

## How requests pick an account

* Accounts are tried in **priority** order (the arrows in the UI).
* An account serves a request only if it **has the model** (a free account has no
  `gemini-pro`, say): such requests go to the account that has it.
* When an account runs out of quota for a model — Gemini says so in the answer, or the
  library raises `UsageLimitExceededError` — the same request is repeated on the next
  account, and the first one is skipped **for that model** until the reset time Gemini
  reported (`GOP_QUOTA_COOLDOWN`, 30 min, when it reported none). Video limits are
  counted apart from chat (`GOP_VIDEO_COOLDOWN`, 6 h: Gemini never reports them).
* When an account's session is lost (logged out, rejected), it is marked **needs
  sign-in** and the request moves on. It stays out until someone signs in again.
* Any other error (refusal, timeout) is the answer: retrying it elsewhere could repeat
  a generation that already went through.
* When every account is used up the client gets `429`; when none is signed in, `503`.

The list in the UI never calls Google: limits are what gemini-webapi read on client
init and after each generation. **Check** re-initialises the client and re-reads them.

## Signing in

**Sign in** clears every cookie in the server's browser and opens Google's sign-in
page in it; the UI shows that browser's screen (noVNC). Sign in, wait for
gemini.google.com to open, press **Done**. The proxy then takes the cookie jar and the
browser **clears all its cookies again**.

That last step matters. A Chrome that still holds the session keeps talking to Google
in the background (account checks, cookie rotation) even with no Google tab open, and
a session rotated by two clients gets revoked within hours. Clearing cookies locally
is not a sign-out: the session stays valid, now held only by the proxy, which rotates
`__Secure-1PSIDTS` and caches it in `GEMINI_COOKIE_PATH` (one file per session).

One browser serves all accounts, one sign-in at a time (an unfinished one lapses after
15 minutes). **Paste cookies** is the fallback: a `name=value; …` string from a desktop
browser. Such sessions are bound to that device and live shorter.

## Storage

`<GOP_DATA_DIR>/accounts.json` (mode 0600) holds the accounts: name, priority, enabled,
email and the jar each was signed in with. Keep the data dir on a volume, together
with `GEMINI_COOKIE_PATH`.

On the first start there is no file and the proxy has one account, `default`, that
reads `GOP_COOKIE_SOURCE` like the single-account proxy did. Signing in to it from the
UI turns it into a stored account.

## Setup

`./scripts/install.sh` does all of this with Docker Compose (proxy + sign-in browser,
`.env` with a generated API key and admin password); the steps below are what it sets up.

1. Run the sign-in browser from [`integrations/cdp-browser`](../integrations/cdp-browser)
   next to the proxy (host network). Besides DevTools on `127.0.0.1:9223` it serves
   noVNC on `127.0.0.1:6080`; both only on loopback.
2. In the proxy:

   ```bash
   GOP_ADMIN_PASSWORD=...           # falls back to GOP_API_KEY when empty
   GOP_CDP_URL=http://127.0.0.1:9223
   GOP_VNC_URL=http://127.0.0.1:6080
   GEMINI_COOKIE_PATH=/app/data/cookie-cache
   ```

3. Behind a reverse proxy, pass WebSocket upgrades for the screen:

   ```nginx
   location /admin/vnc/websockify {
       proxy_pass http://127.0.0.1:4982;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_read_timeout 1h;
   }
   ```

The UI signs in with the password and keeps a signed, HttpOnly cookie for 7 days
(path `/admin`). Scripts can call the same endpoints with `Authorization: Bearer
<GOP_API_KEY>`.

## API

| Method | Path | |
|--------|------|-|
| `GET` | `/admin/accounts` | accounts with state, models, limits, cooldowns |
| `POST` | `/admin/accounts` | add `{"name": ...}` |
| `PATCH` | `/admin/accounts/{id}` | `{"name", "enabled", "priority"}` |
| `DELETE` | `/admin/accounts/{id}` | remove |
| `POST` | `/admin/accounts/{id}/login/start` | open Google sign-in in the server's browser |
| `POST` | `/admin/accounts/{id}/login/finish` | take the session, clear the browser |
| `POST` | `/admin/accounts/{id}/login/cancel` | clear the browser |
| `POST` | `/admin/accounts/{id}/cookies` | `{"cookies": "name=value; ..."}` |
| `POST` | `/admin/accounts/{id}/check` | re-initialise the client (talks to Google) |
| `DELETE` | `/admin/accounts/{id}/cooldowns` | forget noted limits |

States: `ok`, `idle` (not started yet), `needs_login`, `logged_out` (guest session of
the `default` account; re-read once a minute), `error` (init failed; retried once a
minute), `disabled`.
