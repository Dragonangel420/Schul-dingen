"""Container entrypoint: runs the Bash ingest pipeline once a day and gives
a live terminal view of it.

Terminal output (docker logs -f / docker run without -d):
  * everything the pipeline and yt-dlp write to ingest.log, live
  * every STATUS_EVERY finished downloads a database status block that stays on
    screen for STATUS_HOLD_SECONDS (output is buffered in the log, nothing is lost)
  * when the download list is finished: summary, next run, and as the very last
    line the path of the download folder
"""
import datetime as dt
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import settings, util, ytdb

STOP = threading.Event()
WEBUI = {"proc": None}
PIPE = {"proc": None}


# ---------------------------------------------------------------- setup ------
def setup_dirs():
    # /etc/yt-ingest, /var/log/yt-ingest, ... are symlinks into /data. A fresh, empty mount on /data
    # hides the sub-folders created in the image, so create the *targets* here.
    data = os.environ.get("DATA_DIR", "/data")
    if os.path.isdir(data) and not os.access(data, os.W_OK):
        print(f"[supervisor] ERROR: {data} is not writable for uid {os.getuid()}. "
              f"On Linux run: sudo chown -R {os.getuid()}:{os.getgid()} <your data folder>", flush=True)
        sys.exit(1)
    for d in (settings.SPOOL, settings.CONF_DIR, settings.LOG_DIR, settings.STATE_DIR,
              settings.RUN_DIR, settings.LIBRARY):
        try:
            util.ensure_dir(d)
        except OSError as exc:
            print(f"[supervisor] cannot create {d}: {exc}", flush=True)
    # seed default config files once; never overwrite the user's edits
    if os.path.isdir(settings.DEFAULTS_DIR):
        for name in sorted(os.listdir(settings.DEFAULTS_DIR)):
            dest = os.path.join(settings.CONF_DIR, name)
            if not os.path.exists(dest):
                shutil.copyfile(os.path.join(settings.DEFAULTS_DIR, name), dest)
                try:
                    os.chmod(dest, 0o664)
                except OSError:
                    pass
                print(f"[supervisor] created default {dest}", flush=True)


def rotate_logs():
    """Same policy as the log retention policy: daily, keep N. Done at run start."""
    log = settings.LOG_FILE
    if not os.path.exists(log) or os.path.getsize(log) == 0:
        return
    if time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(log))) == time.strftime("%Y-%m-%d"):
        if os.path.getsize(log) < 50 * 1024 * 1024:
            return
    for i in range(settings.LOG_KEEP - 1, 0, -1):
        src, dst = f"{log}.{i}", f"{log}.{i + 1}"
        if os.path.exists(src):
            os.replace(src, dst)
    os.replace(log, f"{log}.1")
    stale = f"{log}.{settings.LOG_KEEP + 1}"
    if os.path.exists(stale):
        os.remove(stale)


# ------------------------------------------------------------- scheduling ----
def parse_schedule(value):
    try:
        hh, mm = value.strip().split(":")
        return int(hh), int(mm)
    except ValueError:
        return 3, 0


def next_run_after(now, schedule):
    hh, mm = parse_schedule(schedule)
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    return candidate


def should_run_on_start(conn):
    mode = settings.RUN_ON_START
    if mode == "always":
        return True
    if mode == "never":
        return False
    last = ytdb.last_run_started_epoch(conn)   # "auto": like systemd Persistent=true
    return last is None or (time.time() - last) > 24 * 3600


# ---------------------------------------------------------------- web UI -----
def start_webui():
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    out = open(settings.WEBUI_LOG, "a", encoding="utf-8")
    WEBUI["proc"] = subprocess.Popen([sys.executable, "-m", "ytapp.webui"], stdout=out, stderr=out)


def check_webui():
    p = WEBUI["proc"]
    if p is not None and p.poll() is not None:
        util.log_line(f"[supervisor] web UI exited with code {p.returncode}, restarting")
        time.sleep(2)
        start_webui()


# --------------------------------------------------------------- state -------
def write_state(**kw):
    state = util.read_json(settings.STATE_FILE, {}) or {}
    state.update(kw)
    try:
        util.write_json(settings.STATE_FILE, state)
    except OSError:
        pass


# ------------------------------------------------------------ yt-dlp update --
def update_ytdlp():
    if not settings.YTDLP_AUTO_UPDATE:
        return
    util.log_line("[supervisor] checking for a newer yt-dlp ...")
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1", TMPDIR="/tmp")
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "--no-deps",
             "--target", settings.PKG_DIR, "yt-dlp", "yt-dlp-ejs"],
            capture_output=True, text=True, timeout=180, env=env)
        if res.returncode == 0:
            util.log_line("[supervisor] yt-dlp is up to date")
        else:
            util.log_line(f"[supervisor] yt-dlp update skipped: {res.stderr.strip().splitlines()[-1:] or 'pip error'}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        util.log_line(f"[supervisor] yt-dlp update skipped: {exc}")


# ---------------------------------------------------------------- the run ----
def status_panel(conn, run_id, started):
    s = ytdb.stats(conn)
    n = ytdb.run_downloads(conn, run_id)
    disk = util.disk_info()
    lines = [
        f"+-- DATABASE STATUS -- {n} new download(s) in this run, {int(time.time() - started)} s elapsed",
        f"| tracked videos : {s['total']}  (downloaded {s['downloaded']}, missing {s['missing']}, pruned {s['pruned']})",
        f"| library size   : {util.human_size(s['bytes'])}",
        f"| disk free      : {util.human_size(disk['free'])} of {util.human_size(disk['total'])}",
    ]
    for v in ytdb.latest_videos(conn, 3, run_id=run_id):
        lines.append(f"| latest         : {v['video_name'][:60]} ({v['channel_name'] or '-'})")
    lines.append(f"+-- holding for {settings.STATUS_HOLD_SECONDS} s ---")
    return lines


class Relay:
    """Prints new lines of ingest.log as they appear (a built-in `tail -f`)."""

    def __init__(self):
        self.start = os.path.getsize(settings.LOG_FILE) if os.path.exists(settings.LOG_FILE) else 0
        self.offset = self.start
        self.buf = b""

    def drain(self):
        try:
            with open(settings.LOG_FILE, "rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
        except OSError:
            return
        self.offset += len(data)
        self.buf += data
        *lines, self.buf = self.buf.split(b"\n")
        for raw in lines:
            print(raw.decode("utf-8", "replace"), flush=True)

    def flush_partial(self):
        self.drain()
        if self.buf:
            print(self.buf.decode("utf-8", "replace"), flush=True)
            self.buf = b""

    def text_since_start(self):
        try:
            with open(settings.LOG_FILE, "rb") as fh:
                fh.seek(self.start)
                return fh.read().decode("utf-8", "replace")
        except OSError:
            return ""


def pump_stdout(proc):
    """A few bash scripts print to stdout (e.g. [cleanup]) instead of the log file.
    Copy those lines into the log so that *everything* ends up in the log file."""
    for raw in iter(proc.stdout.readline, b""):
        util.log_line(raw.decode("utf-8", "replace").rstrip("\n"))


def final_path_line():
    host = settings.host_download_path()
    if host:
        return f"Download folder: {host}   (inside the container: {settings.SPOOL})"
    return (f"Download folder: {settings.SPOOL}   (inside the container; "
            f"browse and play the videos at http://localhost:{settings.WEBUI_PORT}/videos)")


def run_pipeline(conn, trigger):
    """One complete run. Returns (result, relay) - relay is already fully drained."""
    relay = Relay()
    run_id = ytdb.start_run(conn, trigger)
    started = time.time()
    write_state(running=True, run_id=run_id, run_started=util.ts(), trigger=trigger)
    rotate_logs()
    relay.start = relay.offset = os.path.getsize(settings.LOG_FILE) if os.path.exists(settings.LOG_FILE) else 0
    if os.path.exists(settings.STORAGE_FLAG):
        os.remove(settings.STORAGE_FLAG)

    def emit(message):
        util.log_line(message)
        relay.drain()

    emit(f"[supervisor] ===== run #{run_id} started ({trigger}) =====")
    result, note, failures = "ok", None, 0

    # --- storage guard before the run -----------------------------------------
    min_free = settings.MIN_FREE_GB * settings.GB
    free = util.free_bytes()
    if free < min_free and settings.STORAGE_POLICY == "prune":
        emit(f"[storage] only {util.human_size(free)} free - pruning oldest downloads (policy=prune)")
        target = min_free + settings.PRUNE_MARGIN_GB * settings.GB
        for v in ytdb.prune_oldest(conn, settings.SPOOL, target):
            emit(f"[storage] pruned \"{v['video_name']}\" ({util.human_size(v['file_size'])}); its id stays in the database")
        free = util.free_bytes()

    if free < min_free:
        emit(f"[storage] DISK FULL: {util.human_size(free)} free, need {settings.MIN_FREE_GB:g} GB. "
             f"Run skipped - free up space or set STORAGE_POLICY=prune.")
        result, note = "storage_full", "not enough free disk space at start"
    else:
        c = ytdb.reconcile(conn)
        emit(f"[db] reconcile: {c['imported']} imported, {c['relinked']} relinked, "
             f"{c['missing']} missing, {c['restored']} restored")
        update_ytdlp()
        relay.drain()
        result, note, failures = _execute(conn, run_id, started, relay)
        c = ytdb.reconcile(conn)
        emit(f"[db] reconcile after run: {c['imported']} imported, {c['relinked']} relinked, {c['missing']} missing")

    new = ytdb.finish_run(conn, run_id, result, failures, note)
    emit(f"[supervisor] ===== run #{run_id} finished: {result}, {new} new video(s), "
         f"{failures} failed item(s), {int(time.time() - started)} s =====")
    relay.flush_partial()
    return result


def _execute(conn, run_id, started, relay):
    """Start the Bash ingest pipeline and relay its log live."""
    env = dict(os.environ, YT_RUN_ID=str(run_id), PATH="/usr/local/bin:/usr/bin:/bin",
               XDG_CACHE_HOME=os.environ.get("XDG_CACHE_HOME", "/tmp/yt-cache"))
    proc = subprocess.Popen([settings.PIPELINE], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env, start_new_session=True)
    PIPE["proc"] = proc
    pump = threading.Thread(target=pump_stdout, args=(proc,), daemon=True)
    pump.start()

    shown_blocks, storage_abort, last_db_check = 0, False, 0.0
    while proc.poll() is None and not STOP.is_set():
        relay.drain()
        now = time.time()
        if now - last_db_check >= 0.5:
            last_db_check = now
            n = ytdb.run_downloads(conn, run_id)
            if settings.STATUS_EVERY > 0 and n // settings.STATUS_EVERY > shown_blocks:
                shown_blocks = n // settings.STATUS_EVERY
                for line in status_panel(conn, run_id, started):
                    util.log_line(f"[db-status] {line}")
                relay.drain()
                time.sleep(settings.STATUS_HOLD_SECONDS)   # keep the block on screen
            if os.path.exists(settings.STORAGE_FLAG) and not storage_abort:
                storage_abort = True
                util.log_line("[storage] storage-full flag detected - stopping the pipeline")
                relay.drain()
                _terminate(proc)
        time.sleep(0.25)
    if STOP.is_set():
        _terminate(proc)
    proc.wait()
    pump.join(timeout=3)
    relay.drain()

    failures = relay.text_since_start().count("[fail]")
    if storage_abort or os.path.exists(settings.STORAGE_FLAG):
        if settings.STORAGE_POLICY == "prune":
            util.log_line("[storage] policy=prune: old downloads will be pruned at the start of the next run")
        else:
            util.log_line("[storage] policy=pause: nothing was deleted; the run continues at the next schedule")
        return "storage_full", "disk filled up during the run", failures
    if proc.returncode not in (0, None) and not STOP.is_set():
        return "failed", f"pipeline exited with code {proc.returncode}", failures
    return "ok", None, failures


def _terminate(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(40):
        if proc.poll() is not None:
            return
        time.sleep(0.25)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


# ------------------------------------------------------------------ main -----
def banner(next_run, immediate=False):
    print("=" * 72, flush=True)
    print(f" yt-ingest container  |  daily run at {settings.SCHEDULE_TIME} ({time.strftime('%Z') or 'UTC'})", flush=True)
    print(" Web UI: http://localhost:8080  (channel lists, videos, live log)", flush=True)
    print(f" Storage guard: min {settings.MIN_FREE_GB:g} GB free, policy={settings.STORAGE_POLICY}", flush=True)
    when = "starting now" if immediate else f"{next_run:%Y-%m-%d %H:%M}"
    print(f" Next run: {when}", flush=True)
    print("=" * 72, flush=True)


def complete_run(conn, trigger):
    """Run the pipeline once, then print the closing lines. Returns the next scheduled run."""
    run_pipeline(conn, trigger)
    next_run = next_run_after(dt.datetime.now(), settings.SCHEDULE_TIME)
    write_state(running=False, next_run=next_run.strftime("%Y-%m-%d %H:%M"))
    nxt = f"[supervisor] next scheduled run: {next_run:%Y-%m-%d %H:%M}"
    path_line = final_path_line()
    util.log_line(nxt)
    util.log_line(path_line)
    print(nxt, flush=True)
    print(path_line, flush=True)   # always the very last line of a run
    return next_run


def handle_signal(signum, _frame):
    STOP.set()


def main():
    os.umask(0o002)   # same umask as the pipeline scripts: group ytshare may write
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    setup_dirs()
    conn = ytdb.connect()
    ytdb.init(conn)
    ytdb.close_stale_runs(conn)
    for stale in (settings.TRIGGER_FILE, settings.STORAGE_FLAG):
        if os.path.exists(stale):
            os.remove(stale)
    write_state(running=False)
    start_webui()

    now = dt.datetime.now()
    next_run = now if should_run_on_start(conn) else next_run_after(now, settings.SCHEDULE_TIME)
    first_trigger = "startup" if next_run <= now else None
    write_state(next_run=next_run.strftime("%Y-%m-%d %H:%M"), schedule=settings.SCHEDULE_TIME)
    banner(next_run, immediate=first_trigger is not None)

    while not STOP.is_set():
        check_webui()
        triggered = os.path.exists(settings.TRIGGER_FILE)
        if triggered or dt.datetime.now() >= next_run:
            trigger = "manual" if triggered else (first_trigger or "schedule")
            first_trigger = None
            if triggered:
                try:
                    os.remove(settings.TRIGGER_FILE)
                except OSError:
                    pass
            next_run = complete_run(conn, trigger)
        time.sleep(1)

    if PIPE["proc"] is not None and PIPE["proc"].poll() is None:
        _terminate(PIPE["proc"])
    if WEBUI["proc"] is not None:
        WEBUI["proc"].terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
