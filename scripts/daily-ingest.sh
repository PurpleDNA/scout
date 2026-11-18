#!/usr/bin/env bash
# Run `scout ingest` at most once a day. Windows Task Scheduler calls this at every logon and unlock;
# all but the first call of the day exit straight away.
#
#   scripts/daily-ingest.sh          normal use
#   scripts/daily-ingest.sh --force  run even if today's ingest is done
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$ROOT/data/last-ingest"   # holds the date of the last finished ingest, e.g. 2026-11-17
LOG="$ROOT/data/ingest.log"
UV="${UV:-$HOME/.local/bin/uv}"  # full path: Task Scheduler's shell doesn't read .bashrc
TODAY="$(date +%F)"

mkdir -p "$ROOT/data"
exec >>"$LOG" 2>&1

# Logon and unlock can fire together. The second copy leaves quietly.
exec 9>"$ROOT/data/.ingest.lock"
flock -n 9 || exit 0

if [[ "${1:-}" != "--force" && "$(cat "$STAMP" 2>/dev/null)" == "$TODAY" ]]; then
  exit 0
fi

echo "=== $(date '+%F %T') ingest starting"

# Wi-Fi often isn't up yet right after logon or waking. Wait up to 3 minutes for it.
# With no internet every source would fail, so don't count that as today's run.
for _ in $(seq 18); do
  curl -sfI --max-time 5 https://www.google.com >/dev/null && break
  sleep 10
done
if ! curl -sfI --max-time 5 https://www.google.com >/dev/null; then
  echo "no internet, will try again at the next logon or unlock"
  exit 1
fi

cd "$ROOT"
"$UV" run scout ingest
code=$?

# 0 = every source fetched, 1 = some sources failed but the rest were saved. Both count as done:
# a flaky board shouldn't make the whole ingest run again at every unlock.
if [[ $code -le 1 ]]; then
  echo "$TODAY" >"$STAMP"
fi
echo "=== $(date '+%F %T') ingest finished, exit $code"
exit $code
