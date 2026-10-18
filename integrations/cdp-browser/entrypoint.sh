#!/bin/sh
set -e
mkdir -p "$PROFILE_DIR"
# A crash leaves the profile locked to the old container's hostname.
rm -f "$PROFILE_DIR"/Singleton*
Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp &
sleep 1
exec chromium \
    --user-data-dir="$PROFILE_DIR" \
    --remote-debugging-port="$CDP_PORT" \
    --no-sandbox \
    --no-first-run --no-default-browser-check \
    --disable-dev-shm-usage --disable-gpu \
    --disable-background-networking --disable-sync \
    --renderer-process-limit=2 \
    --window-size=1280,900 \
    https://gemini.google.com/app
