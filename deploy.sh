#!/usr/bin/env bash
#
# Copy the dashboard onto a Presto and restart it.
#
#   ./deploy.sh                 # uses /dev/ttyACM0
#   PORT=/dev/ttyACM1 ./deploy.sh
#
# Needs mpremote. With uv it is already in the project's dev group:
#   uv sync
# Otherwise:  pip install mpremote
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-/dev/ttyACM0}"
SELF=$$

# Prefer the project's uv environment, fall back to whatever python has.
if command -v uv >/dev/null 2>&1 && [ -f "$REPO/pyproject.toml" ]; then
    MP="uv run --project $REPO --quiet python -m mpremote connect $PORT"
else
    MP="python3 -m mpremote connect $PORT"
fi

if [ ! -f "$REPO/secrets.py" ]; then
    echo "No secrets.py - copy secrets.example.py to secrets.py and fill it in."
    exit 1
fi

# Clear any stray mpremote holding the port (never this shell).
for p in $(pgrep -f "python3 -m mpremote" 2>/dev/null); do
    [ "$p" = "$SELF" ] && continue
    cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)
    case "$cmd" in python3\ -m\ mpremote*) kill -9 "$p" 2>/dev/null ;; esac
done

echo "== waiting for $PORT =="
for _ in $(seq 1 120); do
    [ -e "$PORT" ] && break
    sleep 1
done
[ -e "$PORT" ] || { echo "no device at $PORT"; exit 1; }

# main.py waits 8s before starting the dashboard, so there is always a window
# in which the board can be reached even if it is already running.
echo "== reaching the board =="
got=0
for i in $(seq 1 90); do
    if timeout 6 $MP exec "print('ok')" 2>/dev/null | grep -q ok; then
        got=1; break
    fi
    sleep 1
done
[ "$got" -eq 0 ] && { echo "could not reach the board - try unplugging it"; exit 1; }

echo "== uploading =="
for f in main.py football_scores.py football_badges.py secrets.py Roboto-Medium.af; do
    for _ in 1 2 3 4 5; do
        if timeout 60 $MP fs cp "$REPO/$f" ":$f" >/dev/null 2>&1; then
            echo "  $f"
            break
        fi
        sleep 1
    done
done

echo "== restarting =="
timeout 30 $MP exec "
import os
try:
    os.remove('/noboot')
except OSError:
    pass
" >/dev/null 2>&1
timeout 20 $MP reset >/dev/null 2>&1
echo "done - the dashboard starts about 8 seconds from now"
