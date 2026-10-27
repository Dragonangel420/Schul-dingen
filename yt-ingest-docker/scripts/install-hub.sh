#!/usr/bin/env bash
# Standalone Docker Hub installer: image + named volume + optional update timer.
# Usage: ./install-hub.sh DOCKERHUB_NAMESPACE/yt-ingest:latest [--auto-update]
set -Eeuo pipefail
usage() { echo "Usage: $0 DOCKERHUB_NAMESPACE/yt-ingest:latest [--auto-update]" >&2; exit 2; }
[[ $# == 1 || $# == 2 ]] || usage
image="$1"
[[ "$image" =~ ^[a-z0-9][a-z0-9._/-]*/yt-ingest:[a-zA-Z0-9._-]+$ ]] || { echo 'Expected a Docker Hub image such as example/yt-ingest:latest' >&2; exit 2; }
[[ $# == 1 || "$2" == '--auto-update' ]] || usage
command -v docker >/dev/null || { echo 'Docker Engine is required.' >&2; exit 1; }
docker compose version >/dev/null || { echo 'Docker Compose v2 is required.' >&2; exit 1; }
project="${YT_INGEST_HOME:-${HOME}/.local/share/yt-ingest}"
mkdir -p "$project"
project="$(cd "$project" && pwd -P)"
[[ "$project" != *$'\n'* && "$project" != *' '* ]] || { echo 'Install path must not contain spaces/newlines.' >&2; exit 2; }

cat > "$project/compose.yaml" <<'COMPOSE'
name: yt-ingest
services:
  yt-ingest:
    image: ${YT_IMAGE:?Set YT_IMAGE in the installer-managed .env file}
    container_name: yt-ingest
    restart: unless-stopped
    stop_grace_period: 30s
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - yt-ingest-data:/data
    environment:
      TZ: ${TZ:-UTC}
      SCHEDULE_TIME: ${SCHEDULE_TIME:-03:00}
      RUN_ON_START: ${RUN_ON_START:-auto}
      MIN_FREE_GB: ${MIN_FREE_GB:-5}
      STORAGE_POLICY: ${STORAGE_POLICY:-pause}
    read_only: true
    tmpfs:
      - /tmp:size=512m,mode=1777,nosuid,nodev
      - /run/yt-ingest:size=1m,uid=1000,gid=1000,mode=0775,nosuid,nodev,noexec
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 512
    mem_limit: 2g
    cpus: 2
    ulimits:
      core: 0
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "3"}
volumes:
  yt-ingest-data:
    name: yt-ingest-data
COMPOSE
if [[ -e "$project/.env" ]]; then
  saved="$(sed -n 's/^YT_IMAGE=//p' "$project/.env" | head -1)"
  if [[ "$saved" != "$image" ]]; then
    echo "Existing deployment uses $saved. Edit $project/.env to change image; no settings were overwritten." >&2
  fi
else
  umask 077
  printf 'YT_IMAGE=%s\nTZ=%s\n' "$image" "${TZ:-UTC}" > "$project/.env"
fi

# Install updater beside Compose: portable, no dependency on the repository.
cat > "$project/update.sh" <<'UPDATE'
#!/usr/bin/env bash
set -Eeuo pipefail
project="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)}"
cd "$project"
exec 9>"$project/.update.lock"
flock -n 9 || { echo 'Another update is already running'; exit 0; }
docker compose pull --quiet yt-ingest
# Do not replace the service while an ingest job is active.
if docker inspect yt-ingest >/dev/null 2>&1 && \
   docker exec yt-ingest /opt/venv/bin/python -c 'import json,sys; p="/run/yt-ingest/state.json"; sys.exit(0 if json.load(open(p)).get("running") else 1)' >/dev/null 2>&1; then
  echo 'Download in progress: pulled image retained; container update deferred.'
  exit 0
fi
docker compose up -d --no-build yt-ingest
UPDATE
chmod 700 "$project/update.sh"

cd "$project"
docker compose pull yt-ingest
docker compose up -d --no-build yt-ingest
printf '\nDeployed %s\nWeb UI: http://127.0.0.1:8080\nData:   Docker volume yt-ingest-data\nFiles:  %s\n' "$image" "$project"

if [[ $# == 2 ]]; then
  command -v systemctl >/dev/null || { echo 'systemd is required for automated updates.' >&2; exit 1; }
  # Install a root-owned updater; systemd must never execute a user-writable script.
  sudo install -m 755 "$project/update.sh" /usr/local/sbin/yt-ingest-update
  tmp_service="$(mktemp)"; tmp_timer="$(mktemp)"
  trap 'rm -f "$tmp_service" "$tmp_timer"' EXIT
  cat > "$tmp_service" <<SERVICE
[Unit]
Description=yt-ingest Docker Hub image update
Wants=network-online.target
After=network-online.target docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/yt-ingest-update ${project}
SERVICE
  cat > "$tmp_timer" <<'TIMER'
[Unit]
Description=Daily yt-ingest image update
[Timer]
OnCalendar=*-*-* 05:20:00
RandomizedDelaySec=20m
Persistent=true
[Install]
WantedBy=timers.target
TIMER
  sudo install -m 644 "$tmp_service" /etc/systemd/system/yt-ingest-update.service
  sudo install -m 644 "$tmp_timer" /etc/systemd/system/yt-ingest-update.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now yt-ingest-update.timer
  echo 'Automatic image updates enabled (daily systemd timer).'
fi
