#!/usr/bin/env bash
# Deploy a checked-out or ZIP-extracted source tree on a Docker-enabled host.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo 'Docker Engine is required.' >&2; exit 1; }
docker compose version >/dev/null || { echo 'Docker Compose v2 is required.' >&2; exit 1; }

# Bind mounts are host-owned; explicitly provision writable directories before Compose.
for dir in data data/downloads data/config data/db data/logs data/library; do
  mkdir -p "$dir"
  if [[ "$(stat -c %u "$dir")" != 1000 || "$(stat -c %g "$dir")" != 1000 ]]; then
    if [[ "$(id -u)" == 0 ]]; then
      chown 1000:1000 "$dir"
    else
      sudo chown 1000:1000 "$dir"
    fi
  fi
done

docker compose up -d --build
echo 'Ready: http://127.0.0.1:8080 (health checks may take a short time)'
echo 'Logs:  docker compose logs -f'
