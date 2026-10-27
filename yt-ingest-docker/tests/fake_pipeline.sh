#!/usr/bin/env bash
# Stand-in for /usr/local/bin/yt-ingest: logs like the real one, prints some lines to stdout
# (like yt-cleanup does) and downloads through the shim, which is called like the real pipeline does.
set -euo pipefail
LOG="$YT_LOG_DIR/ingest.log"
echo "$(date -Is) [archive]" >> "$LOG"
echo "[cleanup] start strict base-name cleanup"
sleep "${FAKE_PIPELINE_DELAY:-0}"
echo "$(date -Is) [start] https://example.invalid/list" >> "$LOG"
"$SHIM" --download-archive "$YT_CONF_DIR/archive.txt" "http://x" >> "$LOG" 2>&1 || echo "$(date -Is) [fail] http://x" >> "$LOG"
echo "$(date -Is) done" >> "$LOG"
