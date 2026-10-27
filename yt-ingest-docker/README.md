# yt-ingest

A self-contained, scheduled YouTube ingestion service for Docker. The project implements its own Bash download stages, Python supervisor, SQLite catalogue and browser-based control interface. It uses **yt-dlp**, **aria2c** and **ffmpeg** to fetch and process media, prevents repeat downloads and applies configurable disk-space safeguards.

**Deployment target:** Debian 12 · Docker Engine / Compose v2 · Linux amd64 and arm64  
**Application version:** 1.0.0 · **Web interface:** `http://127.0.0.1:8080`

## Contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Installation on a new Debian 12 host](#installation-on-a-new-debian-12-host)
4. [Operation and updates](#operation-and-updates)
5. [Configuration](#configuration)
6. [Storage and backup](#storage-and-backup)
7. [Security](#security)
8. [Testing](#testing)
9. [Troubleshooting](#troubleshooting)
10. [Limitations](#limitations)
11. [Repository structure](#repository-structure)

## 1. Architecture

| Component | Responsibility |
|---|---|
| Bash ingest stages | Read URL lists; build archives; clean, deduplicate and rename files; invoke yt-dlp |
| yt-dlp wrapper | Use the SQLite catalogue to suppress repeat downloads and record finished media |
| Supervisor | Start scheduled and manual runs, rotate logs and enforce the storage policy |
| Web interface | Edit channel lists, inspect run history, stream logs and access downloaded videos |
| Persistent storage | Keep media, configuration, logs and SQLite state outside the container |

The service runs as an unprivileged account (UID/GID `1000`). All writable application state is under `/data`; the root filesystem is read-only. A fresh installation seeds three sample YouTube URLs in the 1080p list. Review or remove them in the web interface before the first scheduled run.

## 2. Prerequisites

| Requirement | Details |
|---|---|
| Host | Debian 12 with a user authorized to use Docker |
| Runtime | Docker Engine, Docker Compose v2 for managed installs |
| Architecture | `linux/amd64` or `linux/arm64` |
| Resources | Approximately 2 GB container memory; storage sized for the media library |
| Network | Outbound HTTPS; localhost TCP 8080 for the management interface |
| Permissions | ZIP installation provisions a bind mount for UID/GID 1000; Docker Hub installation uses a managed volume |

Docker access grants extensive control over the host. Only trusted administrators should be added to the `docker` group.

## 3. Installation on a new Debian 12 host

### 3.1 Install Docker Engine

On a clean Debian 12 installation, configure Docker's official APT repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl unzip
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian %s stable\n' \
  "$(dpkg --print-architecture)" "$(. /etc/os-release && echo "$VERSION_CODENAME")" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Start a new login session so the group change takes effect. Check with `docker run --rm hello-world` and `docker compose version`. Alternatively, operate Docker with an administrator account without granting group membership.

### 3.2 Install from the project ZIP

On a fresh Debian 12 host, install Docker Engine and Compose first. If you have already completed Section 3.1, skip this setup and proceed to extraction.

On a clean Debian 12 installation, configure Docker's official APT repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl unzip
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian %s stable\n' \
  "$(dpkg --print-architecture)" "$(. /etc/os-release && echo "$VERSION_CODENAME")" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Start a new login session so the group change takes effect. Check with `docker run --rm hello-world` and `docker compose version`. Alternatively, operate Docker with an administrator account without granting group membership.

Unpack the supplied production archive and enter the project directory:

```bash
unzip yt-ingest-docker-production.zip
cd yt-ingest-docker
```

The equivalent source checkout can also be used. No pre-existing host pipeline is required; the image contains the complete application.

### 3.3 Create the data folder

Create the persistent directories **before** starting the ZIP deployment:

```bash
mkdir -p data/{downloads,config,logs,db,library}
```

The installation script below also creates any missing directories and assigns directory ownership to container UID/GID `1000`. It does not recursively change ownership of existing media. Keep `data/` outside version control and include it in your backup policy.

### 3.4 Build and start from the ZIP

```bash
./scripts/install-local.sh
```

This builds `yt-ingest:local`, provisions `/data`, and starts the service with Compose. No `.env` file or manual Docker image tagging is required. For diagnostics:

```bash
docker compose ps
docker compose logs -f
```

### 3.5 Install from Docker Hub

Docker Engine and Compose must already be installed. Download the standalone `install-hub.sh` from the published release or project repository, then substitute the image namespace in the following command. **Docker Hub image placeholder:** `DOCKERHUB_NAMESPACE/yt-ingest:latest`.

```bash
./install-hub.sh DOCKERHUB_NAMESPACE/yt-ingest:latest --auto-update
```

`install-hub.sh` is provided in the project ZIP under `scripts/` and as a standalone deployment script. It pulls the image, creates a managed Docker volume named `yt-ingest-data`, generates the Compose configuration under `~/.local/share/yt-ingest/`, and starts the service. There is **no manual data-folder creation, UID adjustment, or environment-file setup**. The optional `--auto-update` flag also installs a daily systemd update timer (requires `sudo`); omit it when updates must be approved manually.

For environments where only Docker is available, the following command starts the published image directly without needing any files from this repository:

```bash
docker run -d --name yt-ingest --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 512 --memory 2g --cpus 2 \
  --tmpfs /tmp:size=512m,mode=1777,nosuid,nodev \
  --tmpfs /run/yt-ingest:size=1m,uid=1000,gid=1000,mode=0775,nosuid,nodev,noexec \
  -p 127.0.0.1:8080:8080 -v yt-ingest-data:/data \
  DOCKERHUB_NAMESPACE/yt-ingest:latest
```

Docker creates the named volume and initializes the application state on first start. An image pull alone does not start a service; Docker Engine must already be installed. Use the installer rather than the raw `docker run` command if automated image updates are required.

### 3.6 Verify the deployment

```bash
curl -fsS http://127.0.0.1:8080/health
docker logs --tail 80 yt-ingest
```

`/health` should return JSON containing `"ok":true`. The dashboard is available at `http://127.0.0.1:8080`. Health may take several seconds on the first start while the catalogue and configuration are initialized. On a fresh installation, three sample video URLs are included in the default 1080p list. The service processes them at the next scheduled run or when Run now is selected.

## 4. Operation and updates

The browser interface offers **Overview**, **Channel lists** and **Videos**. Three video URLs are preconfigured in the 1080p list on first installation. Add or remove URLs under Channel lists and select **Run now** to execute an immediate ingest. By default, the scheduled job starts daily at `03:00` in the configured `TZ`; a missed run is recovered after startup.

| Task | Command |
|---|---|
| Inspect container | `docker ps --filter name=yt-ingest` |
| Follow output | `docker logs -f yt-ingest` |
| Trigger a run | `curl -X POST http://127.0.0.1:8080/run-now` |
| Stop / start | `docker stop yt-ingest` / `docker start yt-ingest` |
| Inspect catalogue | `docker exec yt-ingest /opt/venv/bin/python -m ytapp.ytdb status` |

**Local ZIP or Git checkout:** from the project directory, run `./scripts/update-local.sh`. Git checkouts with a configured upstream are fast-forwarded before rebuilding; ZIP deployments require replacing the source files with a newer ZIP first. The script rebuilds and recreates the container without deleting `data/`.

**Docker Hub install:** run `~/.local/share/yt-ingest/update.sh`. It pulls the configured image and recreates the container only when necessary. If a download is active, replacement is deferred. Data remains in the named volume. With `--auto-update`, a systemd timer invokes the same script daily, without user intervention:

```bash
systemctl list-timers yt-ingest-update.timer
sudo systemctl status yt-ingest-update.timer
sudo systemctl start yt-ingest-update.service  # test the update procedure now
```

For unattended environments, pin a version tag or digest and advance it through a controlled release process. Automatically tracking `latest` may introduce breaking changes. Back up persistent state before deploying a new image version.

To stop without deleting data use `docker stop yt-ingest` or, for a managed installation, `docker compose down` in its deployment directory. **Never run `docker volume rm yt-ingest-data` unless permanent deletion is intended.**

## 5. Configuration

Defaults are set in `docker-compose.yml` for ZIP installations, or `~/.local/share/yt-ingest/.env` for Docker Hub installations. The web editor manages `channels_1080p.conf`, `channels_2160p.conf` and `channels_curated.conf`; it validates URLs and preserves a backup of edited lists. Each file accepts one YouTube URL per line, with `#` for comments. New installations seed `channels_1080p.conf` with these three videos:

```text
https://www.youtube.com/watch?v=PVGeM40dABA
https://www.youtube.com/watch?v=UXqq0ZvbOnk
https://www.youtube.com/watch?v=u9lj-c29dxI
```

Existing configuration files are not overwritten when an image is updated.

| Setting | Default | Purpose |
|---|---|---|
| `TZ` | `UTC` | Local time zone for the schedule |
| `SCHEDULE_TIME` | `03:00` | Daily start in `HH:MM` |
| `RUN_ON_START` | `auto` | `auto`, `always` or `never` |
| `MIN_FREE_GB` | `5` | Minimum free space before a download starts |
| `STORAGE_POLICY` | `pause` | Stop when full; `prune` deletes the oldest downloaded media |
| `PRUNE_MARGIN_GB` | `2` | Additional free space target when pruning |
| `YTDLP_AUTO_UPDATE` | `1` | Update yt-dlp inside the data volume when a run starts |
| `LOG_KEEP` | `14` | Rotated ingestion logs retained |

For ZIP deployments, set overrides under the service `environment:` block and run `docker compose up -d`. For Docker Hub installations, edit `.env` and run `~/.local/share/yt-ingest/update.sh`. Other settings, including status reporting and web bind addresses, are defined in `ytapp/settings.py`.

The management endpoint is bound to `127.0.0.1` on the host. From a remote computer, use an SSH tunnel (for example `ssh -L 8080:127.0.0.1:8080 user@server`) rather than exposing port 8080 directly. The application does not include login authentication.

## 6. Storage and backup

| Container path | Contents |
|---|---|
| `/data/downloads/Automatic/` | Downloaded media, grouped by channel |
| `/data/config/` | Channel lists, yt-dlp profiles and generated archive |
| `/data/db/youtube_downloads.db` | SQLite catalogue and run history |
| `/data/logs/` | Ingestion log, rotated logs and web errors |
| `/data/library/` | Optional external library scan path |

ZIP installs use the host directory `./data`. Docker Hub installs use the named volume `yt-ingest-data`; its location is managed by Docker. Mount an existing video library read-only at `/data/library` if needed.

Back up the database, configuration and downloaded media. For a consistent SQLite backup, stop the container first or use SQLite's online backup API. Replacing an image does not delete the volume, but deleting a volume is irreversible without backup. Media removed by the optional `prune` policy is not automatically downloaded again because the catalogue retains its identifiers.

## 7. Security

The image runs without root privileges, drops Linux capabilities, applies `no-new-privileges`, uses a read-only root filesystem, mounts temporary writable directories as tmpfs and limits resources. The web interface includes URL validation, cross-origin request checks, constrained file access and restrictive security headers.

**Access control:** the web UI has no login or TLS. Keep it on loopback; use SSH or an authenticated HTTPS reverse proxy for remote access. **Updates:** yt-dlp can self-update at runtime, while the Docker image is maintained separately. Pin dependencies and review released versions where auditability is required. These measures support hardening but do not represent a third-party security certification.

## 8. Testing

For source-level tests, use a Python 3 virtual environment (`python3-venv` and `python3-pip` on Debian):

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest
```

| Test | Command | Scope |
|---|---|---|
| Unit and component tests | `python -m pytest -q` | Database, scheduler, interface, URL validation and wrapper |
| Script integrity | `(cd pipeline && sha256sum -c SHA256SUMS)` | Validate packaged Bash stages and yt-dlp profiles |
| Local image smoke test | `tests/smoke.sh yt-ingest:local` | Runtime health, initialization and bundled tools |
| Published image acceptance | `./test-dockerhub.sh DOCKERHUB_NAMESPACE/yt-ingest:latest` | Fresh named volume, permissions, health, security and persistence |

The **standalone Docker Hub acceptance test** is supplied separately as `test-dockerhub.sh` and requires only Docker and `curl`. It does not contact YouTube, verifies all three seeded URLs, creates temporary resources, and cleans them up after completion. The test does not verify media download success. Functional acceptance requires adding an authorized test URL and reviewing the resulting file and catalogue entry.

## 9. Troubleshooting

| Symptom | Action |
|---|---|
| Docker socket permission denied | Log in again after joining the `docker` group, or use an authorized administrator account |
| Container restarts with `/data` permission errors | For ZIP installs, rerun `./scripts/install-local.sh`; inspect ownership of any pre-existing files |
| Health endpoint unavailable | Check `docker logs yt-ingest`, port 8080 availability and Docker health status |
| Run skipped for insufficient space | Free storage or change `MIN_FREE_GB`; enabling `prune` authorizes deletion of older media |
| YouTube returns HTTP 403 or bot detection | Check network access, yt-dlp logs and upstream compatibility; successful downloads cannot be guaranteed |
| A channel produces no downloads | Ensure its URL is valid, the list ends with a newline and a scheduled or manual run has occurred |
| Changed Hub `.env` not applied | Run the installed `update.sh` script to reconcile the container |

## 10. Limitations

Downloads depend on upstream availability, copyright restrictions and changes to YouTube. Automatic yt-dlp upgrades improve compatibility but reduce strict reproducibility. The web interface does not provide authentication or TLS, only one ingest job runs at a time, and media playback depends on browser codec support. Debian 12 is the documented deployment target; Docker Desktop behavior is not part of the acceptance matrix.

## 11. Repository structure

| Path | Description |
|---|---|
| `Dockerfile`, `docker-compose.yml` | Image build and local deployment |
| `pipeline/`, `defaults/` | Bash ingest stages, checksums and seed configuration |
| `ytapp/` | Supervisor, SQLite catalogue, yt-dlp wrapper and web interface |
| `scripts/` | ZIP installer, standalone Docker Hub installer and source updater |
| `tests/` | Unit tests and deployment smoke checks |
| `.github/workflows/docker.yml` | CI tests and multi-architecture image publishing |

