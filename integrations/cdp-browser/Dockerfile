# Chromium for signing in to Google on the server (GOP_COOKIE_SOURCE=cdp).
# Runs headful on a virtual display (Xvfb): Google sign-in treats headless Chrome
# as a bot. DevTools and noVNC listen on 127.0.0.1 only: sign in from the proxy's
# admin UI (/admin/ui proxies noVNC) or through an SSH tunnel.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium xvfb x11vnc novnc websockify \
        fonts-liberation fonts-noto-color-emoji ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 chrome

COPY entrypoint.sh /entrypoint.sh
USER chrome
ENV DISPLAY=:99 CDP_PORT=9223 VNC_PORT=6080 PROFILE_DIR=/data/profile
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
