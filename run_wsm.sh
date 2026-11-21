#!/usr/bin/env bash
# run_wsm.sh - start WSM (capture -> decode -> SQLite -> web page). One instance only; Ctrl-C or SIGTERM stops it.
# Usage:  ./run_wsm.sh [-c wsm.yaml] [--sim]
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for v in "$DIR/../.venv" "$DIR/.venv"; do
    [ -f "$v/bin/activate" ] && { . "$v/bin/activate"; break; }
done
OLD="$(pgrep -f '^python3? [^ ]*wsm_service\.py' || true)"
if [ -n "$OLD" ]; then
    echo "[run_wsm] already running (PID $(echo $OLD | tr '\n' ' ')): stopping it first" >&2
    kill $OLD 2>/dev/null || true
    for _ in $(seq 1 20); do pgrep -f '^python3? [^ ]*wsm_service\.py' >/dev/null || break; sleep 1; done
    pkill -9 -f '^python3? [^ ]*wsm_service\.py' 2>/dev/null || true
fi
cd "$DIR"
exec python3 "$DIR/wsm_service.py" "$@"
