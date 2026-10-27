"""yt-dlp shim - installed as /usr/local/bin/yt-dlp, ahead of the real yt-dlp in PATH.

Why a shim? The bash pipeline uses shell stages, while the
database has to become the primary duplicate protection. The pipeline calls
plain `yt-dlp`, so this wrapper sits in between and, for every call:

  1. refuses to start when the disk is (nearly) full            -> storage guard
  2. rewrites the --download-archive file FROM THE DATABASE     -> DB is primary
  3. runs the real yt-dlp with a few extra output options
  4. records every finished video in the database               -> via --print after_move
  5. watches free disk space while downloading                  -> stops cleanly

Everything it prints goes to stdout/stderr, which the ingest pipeline
already appends to /var/log/yt-ingest/ingest.log.
"""
import json
import os
import shlex
import signal
import subprocess
import sys
import threading

from . import settings, util, ytdb

MARKER = "@@YTDB@@"
FIELDS = "id,extractor_key,title,channel,duration,webpage_url,filepath"
PASSTHROUGH_FLAGS = {"--version", "-h", "--help", "-U", "--update", "--list-extractors"}
EXIT_STORAGE = 3


def say(message):
    print(f"{util.ts()} {message}", flush=True)


def option_value(args, name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def real_command():
    custom = os.environ.get("YTDLP_CMD")
    if custom:
        return shlex.split(custom)
    return [sys.executable, "-m", "yt_dlp"]


def real_env():
    env = dict(os.environ)
    env.setdefault("XDG_CACHE_HOME", "/tmp/yt-cache")  # rootfs may be read-only
    # Prefer the self-updated yt-dlp (if present) over the one baked into the image.
    if os.path.isdir(settings.PKG_DIR):
        env["PYTHONPATH"] = settings.PKG_DIR + os.pathsep + env.get("PYTHONPATH", "")
    return env


def storage_blocked():
    """Return a reason string if no new download may start, else None."""
    if os.path.exists(settings.STORAGE_FLAG):
        return "storage-full flag is set for this run"
    free = util.free_bytes()
    if free < settings.MIN_FREE_GB * settings.GB:
        return f"only {util.human_size(free)} free (minimum {settings.MIN_FREE_GB:g} GB)"
    return None


def set_storage_flag(free):
    try:
        os.makedirs(settings.RUN_DIR, exist_ok=True)
        util.write_json(settings.STORAGE_FLAG, {"free": free, "at": util.ts()})
    except OSError:
        pass


def handle_line(conn, line, run_id):
    """Process one output line of the real yt-dlp. Returns the line to log (or None)."""
    if not line.startswith(MARKER):
        return line
    try:
        info = json.loads(line[len(MARKER):])
    except ValueError:
        return f"[db] could not parse record: {line.strip()[:200]}\n"
    path = info.get("filepath")
    size = os.path.getsize(path) if path and os.path.exists(path) else None
    vid = info.get("id")
    if not vid:
        return None
    ext = (info.get("extractor_key") or "youtube").lower()
    new = ytdb.record_video(conn, vid, info.get("title"), info.get("channel"),
                            info.get("duration"), size, path, info.get("webpage_url"),
                            extractor=ext, run_id=run_id)
    verb = "recorded" if new else "updated"
    return f"{util.ts()} [db] {verb} {ext} {vid} \"{info.get('title')}\" ({util.human_size(size)})\n"


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = real_command()

    if PASSTHROUGH_FLAGS & set(args):
        os.execvpe(cmd[0], cmd + args, real_env())

    conn = ytdb.connect()
    ytdb.init(conn)
    run_id = int(os.environ["YT_RUN_ID"]) if os.environ.get("YT_RUN_ID", "").isdigit() else None

    reason = storage_blocked()
    if reason:
        say(f"[storage] not downloading: {reason}")
        set_storage_flag(util.free_bytes())
        return EXIT_STORAGE

    archive = option_value(args, "--download-archive")
    if archive:
        n = ytdb.export_archive(conn, archive)
        say(f"[db] duplicate protection: {n} known videos written from the database to {archive}")

    extra = [
        "--newline",                       # one progress update per line (log friendly)
        "--progress-delta", "5",           # ... at most every 5 seconds
        "--print", f"after_move:{MARKER}%(.{{{FIELDS}}})j",
        "--no-quiet",                      # --print would otherwise silence normal output
    ]
    proc = subprocess.Popen(cmd + args + extra, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, errors="replace", env=real_env(),
                            start_new_session=True)

    def stop_child(*_):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
    signal.signal(signal.SIGTERM, lambda *a: (stop_child(), sys.exit(143)))
    signal.signal(signal.SIGINT, lambda *a: (stop_child(), sys.exit(130)))

    aborted = {"storage": False}
    done = threading.Event()

    def watchdog():
        hard = settings.MIN_FREE_GB * settings.GB * 0.5
        while not done.wait(10):
            free = util.free_bytes()
            if free < hard:
                aborted["storage"] = True
                say(f"[storage] only {util.human_size(free)} left - stopping yt-dlp (partial files are kept and resume later)")
                set_storage_flag(free)
                stop_child()
                return
    threading.Thread(target=watchdog, daemon=True).start()

    try:
        for line in proc.stdout:
            out = handle_line(conn, line, run_id)
            if out is not None:
                sys.stdout.write(out if out.endswith("\n") else out + "\n")
                sys.stdout.flush()
    finally:
        done.set()
        proc.wait()
    return EXIT_STORAGE if aborted["storage"] else proc.returncode


if __name__ == "__main__":
    sys.exit(main())
