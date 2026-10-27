#!/usr/bin/env bash
# Smoke test for the locally built image or a supplied published image.
# Usage: tests/smoke.sh [image]; without argument builds yt-ingest:test first.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
image="${1:-yt-ingest:test}"
if [[ $# -eq 0 ]]; then docker build -t "$image" .; fi
exec ./tests/test-dockerhub.sh "$image"
