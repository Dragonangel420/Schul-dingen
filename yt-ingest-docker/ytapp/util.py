"""Small helpers shared by the shim, supervisor and web UI."""
import os
import shutil
import time
from datetime import datetime

from . import settings


def ts():
    """Timestamp in the same format as the pipeline's own log() (date -Is)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log_line(message, path=None):
    """Append one timestamped line to the pipeline log (same file the bash scripts use)."""
    path = path or settings.LOG_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{ts()} {message}\n")


def ensure_dir(path):
    """mkdir -p that also works when `path` is a symlink into a mounted folder whose
    target does not exist yet (fresh, empty bind mount hides the folders made in the image)."""
    os.makedirs(os.path.realpath(path), exist_ok=True)


def human_size(n):
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def human_duration(seconds):
    if seconds is None:
        return "-"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def free_bytes(path=None):
    path = path or settings.SPOOL
    os.makedirs(path, exist_ok=True)
    return shutil.disk_usage(path).free


def disk_info(path=None):
    path = path or settings.SPOOL
    os.makedirs(path, exist_ok=True)
    u = shutil.disk_usage(path)
    return {"total": u.total, "free": u.free, "used": u.total - u.free}


def read_json(path, default=None):
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    import json
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def now_epoch():
    return int(time.time())
