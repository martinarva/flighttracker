#!/bin/sh
# Start a virtual X display, then run the app. Chromium runs HEADFUL on this display
# (headless is blocked by the site's Imperva bot check).
#
# Xvfb is SUPERVISED: a stale lock is cleared before each start, and the server is
# restarted if it ever dies — otherwise a single Xvfb crash (or a leftover
# /tmp/.X99-lock surviving a container `restart`) would silently break every scrape
# while the app kept running.

DISP=99
mkdir -p /tmp/.X11-unix 2>/dev/null || true
chmod 1777 /tmp/.X11-unix 2>/dev/null || true

( while true; do
    rm -f "/tmp/.X${DISP}-lock" "/tmp/.X11-unix/X${DISP}" 2>/dev/null || true
    Xvfb ":${DISP}" -screen 0 1280x1024x24 -nolisten tcp -ac >/tmp/xvfb.log 2>&1
    echo "[entrypoint] Xvfb exited, restarting in 1s" >&2
    sleep 1
  done ) &

export DISPLAY=":${DISP}"

# wait for the display socket before starting the app
i=0
while [ ! -e "/tmp/.X11-unix/X${DISP}" ] && [ "$i" -lt 50 ]; do i=$((i + 1)); sleep 0.2; done

exec uvicorn app.main:get_app --factory --host 0.0.0.0 --port 8080
