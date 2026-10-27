# syntax=docker/dockerfile:1
# yt-ingest: scheduled downloader, SQLite index, and web interface on Debian 12.
FROM debian:12-slim

ARG YT_UID=1000
ARG YT_GID=1000

LABEL org.opencontainers.image.title="yt-ingest" \
      org.opencontainers.image.description="Daily yt-dlp download pipeline with database, web UI and storage guard" \
      org.opencontainers.image.base.name="debian:12-slim"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    TZ=UTC \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    XDG_CACHE_HOME=/tmp/yt-cache \
    DENO_DIR=/tmp/deno

# --- system packages (kept minimal) -------------------------------------------------
# ffmpeg: remux to mkv | aria2: external downloader | tini: PID 1 | procps: ps/pkill from the docs
# curl+unzip are only needed to fetch deno (JavaScript runtime yt-dlp needs for YouTube) and are removed again.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv ffmpeg aria2 ca-certificates tzdata procps tini passwd curl unzip \
 && arch="$(dpkg --print-architecture)" \
 && case "$arch" in amd64) deno_arch=x86_64;; arm64) deno_arch=aarch64;; *) echo "unsupported arch $arch" >&2; exit 1;; esac \
 && curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${deno_arch}-unknown-linux-gnu.zip" -o /tmp/deno.zip \
 && unzip -q /tmp/deno.zip -d /usr/local/bin && chmod 755 /usr/local/bin/deno && rm /tmp/deno.zip \
 && apt-get purge -y curl unzip \
 && rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/* /var/cache/apt/*

# --- Python environment ---------------------------------------------------------------
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# --- service user + folders, non-root account and persistent storage ----------
RUN for c in groupadd useradd passwd mkdir ln chown chmod; do \
      command -v "$c" >/dev/null || { echo "MISSING TOOL: $c (PATH=$PATH)" >&2; exit 1; }; \
    done \
 && groupadd -g "${YT_GID}" ytshare \
 && useradd -r -l -u "${YT_UID}" -g ytshare -s /usr/sbin/nologin -d /nonexistent yt-ingest \
 && passwd -l root \
 && mkdir -p /data/downloads /data/config /data/logs /data/db /data/library \
              /run/yt-ingest \
              /usr/share/yt-ingest/defaults \
 && ln -s /data/config    /etc/yt-ingest \
 && ln -s /data/logs      /var/log/yt-ingest \
 && ln -s /data/db        /var/lib/yt-ingest \
 && chown yt-ingest:ytshare /data /data/* /run/yt-ingest \
 && chmod 2775 /data /data/* /run/yt-ingest

# --- verify the bundled ingest scripts before installing them ----
COPY pipeline /tmp/pipeline
RUN cd /tmp/pipeline && sha256sum -c SHA256SUMS \
 && install -m 755 bin/* /usr/local/bin/ \
 && install -m 644 conf/* /usr/share/yt-ingest/defaults/ \
 && rm -rf /tmp/pipeline
COPY defaults/ /usr/share/yt-ingest/defaults/

# --- Python wrapper: database, yt-dlp shim, scheduler, web UI -------------------------
COPY ytapp /opt/ytapp
COPY --chmod=755 docker/yt-dlp /usr/local/bin/yt-dlp

# --- hardening (see README, section "Security / CIS") ---------------------------------
# no setuid/setgid binaries, no login for root
RUN find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} + 2>/dev/null || true

USER ${YT_UID}:${YT_GID}
WORKDIR /opt
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD ["/opt/venv/bin/python", "-m", "ytapp.healthcheck"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/venv/bin/python", "-m", "ytapp.supervisor"]
