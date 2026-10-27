#!/usr/bin/env bash
# Standalone Docker Hub acceptance test: no local project files or persistent setup required.
# Usage: ./test-dockerhub.sh NAMESPACE/yt-ingest:latest
set -Eeuo pipefail
[[ $# -eq 1 ]] || { echo "Usage: $0 NAMESPACE/yt-ingest:latest" >&2; exit 2; }
image="$1"
for cmd in docker curl; do command -v "$cmd" >/dev/null || { echo "Missing dependency: $cmd" >&2; exit 1; }; done
name="yt-ingest-test-$$"
volume="yt-ingest-test-$$"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
docker volume create "$volume" >/dev/null
docker run -d --name "$name" --restart no \
  --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 \
  --tmpfs /tmp:size=128m,mode=1777,nosuid,nodev \
  --tmpfs /run/yt-ingest:size=1m,uid=1000,gid=1000,mode=0775,nosuid,nodev,noexec \
  -p 127.0.0.1::8080 -v "$volume:/data" -e RUN_ON_START=never "$image" >/dev/null

endpoint="$(docker port "$name" 8080/tcp | head -n 1)"
url="http://${endpoint}/health"
ready=0
for attempt in $(seq 1 60); do
  if curl --silent --show-error --fail --max-time 3 "$url" >/dev/null 2>&1; then ready=1; break; fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "$name")" != true ]]; then
    docker logs "$name"; echo 'FAIL: container terminated' >&2; exit 1
  fi
  sleep 1
done
[[ "$ready" == 1 ]] || { docker logs "$name"; echo 'FAIL: /health not ready' >&2; exit 1; }
check() { desc="$1"; shift; if "$@"; then echo "PASS: $desc"; else docker logs "$name" | tail -50; echo "FAIL: $desc" >&2; exit 1; fi; }
check 'health endpoint' curl --silent --fail --max-time 5 "$url"
check 'unprivileged process (UID 1000)' bash -c "[[ \"\$(docker exec '$name' id -u)\" == 1000 ]]"
check 'automatic config initialization' docker exec "$name" test -s /data/config/channels_1080p.conf
check 'automatic database initialization' docker exec "$name" test -s /data/db/youtube_downloads.db
# Verify the published image carries all three configured default downloads.
for video_id in PVGeM40dABA UXqq0ZvbOnk u9lj-c29dxI; do
  check "seeded video ${video_id}" docker exec "$name" grep -Fxq "https://www.youtube.com/watch?v=${video_id}" /data/config/channels_1080p.conf
done
check 'data directory writable by service' docker exec "$name" /bin/bash -c 'touch /data/.writecheck && rm /data/.writecheck'
check 'yt-dlp available' docker exec "$name" yt-dlp --version
check 'dependencies installed' docker exec "$name" /bin/bash -c 'command -v aria2c && command -v ffmpeg && command -v deno'
check 'no setuid binaries' docker exec "$name" /bin/bash -c '! find / -xdev -type f -perm -4000 2>/dev/null | grep -q .'
check 'loopback-only port mapping' bash -c "[[ \"\$(docker port '$name' 8080/tcp)\" == 127.0.0.1:* ]]"
# Verify a restart does not reset a customer edit.
docker exec "$name" /bin/bash -c "printf '# kept-after-restart\\n' >> /data/config/channels_2160p.conf"
docker restart "$name" >/dev/null
check 'customer configuration persists across restarts' docker exec "$name" grep -q 'kept-after-restart' /data/config/channels_2160p.conf
printf '\nAll Docker Hub deployment checks passed. Test volume and container were temporary.\n'
