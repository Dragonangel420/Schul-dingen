#!/usr/bin/env bash
# Pull the latest Git commit if a remote exists, then rebuild and deploy.
# ZIP installations must be replaced with a newer ZIP before running this command.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -d .git ]] && git remote get-url origin >/dev/null 2>&1; then
  [[ -z "$(git status --porcelain)" ]] || { echo 'Working tree has local changes. Commit or stash before updating.' >&2; exit 1; }
  git pull --ff-only
else
  echo 'No Git upstream. Re-extract a newer project ZIP to update application code.'
fi
docker compose build --pull
docker compose up -d --no-build
