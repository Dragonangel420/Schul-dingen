"""Central configuration. Every path can be overridden through the environment
(used by the tests); inside the container the defaults use service-owned paths under /data."""
import os


def _env(name, default):
    return os.environ.get(name, default)


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return int(default)


# --- paths (identical to the paths hard-coded in the ingest bash scripts) ---
SPOOL = _env("YT_SPOOL", "/data/downloads/Automatic")
LIBRARY = _env("YT_LIBRARY", "/data/library")
CONF_DIR = _env("YT_CONF_DIR", "/etc/yt-ingest")
LOG_DIR = _env("YT_LOG_DIR", "/var/log/yt-ingest")
STATE_DIR = _env("YT_STATE_DIR", "/var/lib/yt-ingest")
RUN_DIR = _env("YT_RUN_DIR", "/run/yt-ingest")
DEFAULTS_DIR = _env("YT_DEFAULTS_DIR", "/usr/share/yt-ingest/defaults")
PIPELINE = _env("YT_PIPELINE", "/usr/local/bin/yt-ingest")

LOG_FILE = os.path.join(LOG_DIR, "ingest.log")
WEBUI_LOG = os.path.join(LOG_DIR, "webui.log")
ARCHIVE = os.path.join(CONF_DIR, "archive.txt")
DB_PATH = _env("YT_DB_PATH", os.path.join(STATE_DIR, "youtube_downloads.db"))
PKG_DIR = os.path.join(STATE_DIR, "ytdlp-pkg")  # self-updated yt-dlp lives here

TRIGGER_FILE = os.path.join(RUN_DIR, "run-now")
STORAGE_FLAG = os.path.join(RUN_DIR, "storage-full")
STATE_FILE = os.path.join(RUN_DIR, "state.json")

CHANNEL_LISTS = ("channels_1080p.conf", "channels_2160p.conf", "channels_curated.conf")

# --- behaviour -----------------------------------------------------------------
SCHEDULE_TIME = _env("SCHEDULE_TIME", "03:00")      # daily start, local time (TZ)
RUN_ON_START = _env("RUN_ON_START", "auto")         # auto | always | never
STATUS_EVERY = _int("STATUS_EVERY", 5)              # DB status block every N downloads
STATUS_HOLD_SECONDS = _int("STATUS_HOLD_SECONDS", 5)
LOG_KEEP = _int("LOG_KEEP", 14)                     # rotated logs kept
YTDLP_AUTO_UPDATE = _env("YTDLP_AUTO_UPDATE", "1") == "1"

# --- storage guard ---------------------------------------------------------------
MIN_FREE_GB = _float("MIN_FREE_GB", "5")            # do not start new downloads below this
STORAGE_POLICY = _env("STORAGE_POLICY", "pause")    # pause | prune
PRUNE_MARGIN_GB = _float("PRUNE_MARGIN_GB", "2")    # prune until MIN_FREE_GB + margin is free

# --- web UI ------------------------------------------------------------------
WEBUI_HOST = _env("WEBUI_HOST", "0.0.0.0")
WEBUI_PORT = _int("WEBUI_PORT", "8080")
HOST_DATA_DIR = _env("HOST_DATA_DIR", "")           # only used to print the host path
HOST_DOWNLOAD_DIR = _env("HOST_DOWNLOAD_DIR", "")   # same, if the downloads are mounted separately


def host_download_path():
    """Where the downloads are on the *host* (display only; the container cannot know this itself)."""
    if HOST_DOWNLOAD_DIR:
        return os.path.join(HOST_DOWNLOAD_DIR.rstrip("/"), "Automatic")
    if HOST_DATA_DIR:
        return os.path.join(HOST_DATA_DIR.rstrip("/"), "downloads", "Automatic")
    return ""

GB = 1024 ** 3
VIDEO_EXTS = (".mkv", ".mp4", ".webm", ".m4v", ".mov")
