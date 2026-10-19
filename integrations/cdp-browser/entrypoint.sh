#!/bin/sh
set -e
mkdir -p "$PROFILE_DIR"
# A crash or a host reboot leaves stale locks: the profile's (old hostname) and
# Xvfb's (a pid that may now belong to another process). Either one stops the start.
rm -f "$PROFILE_DIR"/Singleton* /tmp/.X99-lock /tmp/.X11-unix/X99
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
    about:blank
