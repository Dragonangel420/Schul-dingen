"""SQLite video database - the primary protection against duplicate downloads.

Schema is an extension of the table from the colleague's script
(video_name, channel_name, video_length, file_size, file_path, video_url,
downloaded_at). Added: video_id/extractor (the real duplicate key), status,
run_id and source.

Design notes
* The database is the source of truth. yt-dlp still needs an archive file, so
  `export_archive()` regenerates archive.txt *from the database* before every
  yt-dlp call (see shim.py). A video stays "known" even after its file was
  moved to another folder or pruned to free disk space.
* Journal mode is the SQLite default (rollback journal), not WAL: WAL needs
  shared memory that is unreliable on Docker Desktop bind mounts (macOS).
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
import time

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT    NOT NULL,
    extractor     TEXT    NOT NULL DEFAULT 'youtube',
    video_name    TEXT    NOT NULL,
    channel_name  TEXT,
    video_length  INTEGER,
    file_size     INTEGER,
    file_path     TEXT,
    video_url     TEXT,
    status        TEXT    NOT NULL DEFAULT 'downloaded',  -- downloaded | missing | pruned
    source        TEXT    NOT NULL DEFAULT 'download',    -- download | import
    run_id        INTEGER,
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (extractor, video_id)
);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos (channel_name);
CREATE INDEX IF NOT EXISTS idx_videos_run ON videos (run_id);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    trigger     TEXT,
    result      TEXT,        -- running | ok | storage_full | skipped | failed
    new_videos  INTEGER DEFAULT 0,
    failures    INTEGER DEFAULT 0,
    note        TEXT
);
"""

# Same ID pattern as the ingest pipeline scripts: "... [<11 chars>].ext"
FILE_ID_RE = re.compile(r"^(?P<title>.*) \[(?P<id>[A-Za-z0-9_-]{11})\]\.(?P<ext>[A-Za-z0-9]+)$")


def utc_now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def connect(path=None):
    path = path or settings.DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init(conn):
    conn.executescript(SCHEMA)
    conn.commit()


# ----------------------------------------------------------------- videos ----
def record_video(conn, video_id, title, channel=None, duration=None, size=None,
                 path=None, url=None, extractor="youtube", run_id=None,
                 source="download", downloaded_at=None):
    """Insert or update one video. Returns True if the video was new."""
    existed = conn.execute(
        "SELECT 1 FROM videos WHERE extractor=? AND video_id=?",
        (extractor, video_id)).fetchone() is not None
    conn.execute(
        """
        INSERT INTO videos (video_id, extractor, video_name, channel_name, video_length,
                            file_size, file_path, video_url, status, source, run_id,
                            downloaded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'downloaded', ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        ON CONFLICT (extractor, video_id) DO UPDATE SET
            video_name   = excluded.video_name,
            channel_name = COALESCE(excluded.channel_name, channel_name),
            video_length = COALESCE(excluded.video_length, video_length),
            file_size    = COALESCE(excluded.file_size, file_size),
            file_path    = COALESCE(excluded.file_path, file_path),
            video_url    = COALESCE(excluded.video_url, video_url),
            status       = 'downloaded',
            source       = CASE WHEN excluded.source = 'download' THEN 'download' ELSE source END,
            run_id       = COALESCE(excluded.run_id, run_id)
        """,
        (video_id, extractor, title or "Unknown", channel, duration, size, path, url,
         source, run_id, downloaded_at))
    conn.commit()
    return not existed


def known_ids(conn):
    """Every video the system must never download again (any status)."""
    return [(r["extractor"], r["video_id"]) for r in conn.execute(
        "SELECT extractor, video_id FROM videos ORDER BY id")]


def export_archive(conn, archive_path):
    """Regenerate the yt-dlp archive file from the database (atomic replace)."""
    ids = known_ids(conn)
    tmp = f"{archive_path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        for extractor, vid in ids:
            fh.write(f"{extractor} {vid}\n")
    try:
        os.chmod(tmp, 0o664)
    except OSError:
        pass
    os.replace(tmp, archive_path)
    return len(ids)


# -------------------------------------------------------------- reconcile ----
def scan_files(roots):
    """Index video files by YouTube id. First hit wins; later roots do not override."""
    found = {}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                m = FILE_ID_RE.match(name)
                if not m or not name.lower().endswith(settings.VIDEO_EXTS):
                    continue
                vid = m.group("id")
                if vid in found:
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(dirpath, root)
                channel = rel.split(os.sep)[0] if rel != "." else None
                found[vid] = {"path": full, "title": m.group("title"), "channel": channel}
    return found


def reconcile(conn, spool=None, library=None):
    """Bring the database in line with the files on disk.

    * unknown files with an id in the name are imported (so they are never re-downloaded)
    * rows whose file was renamed/moved get the new path (the dashcam rename script does this)
    * rows whose file vanished are marked 'missing' - the id stays known on purpose
    """
    spool = spool or settings.SPOOL
    library = library or settings.LIBRARY
    index = scan_files([spool, library])
    counts = {"imported": 0, "relinked": 0, "missing": 0, "restored": 0}

    rows = conn.execute("SELECT id, video_id, extractor, file_path, status FROM videos").fetchall()
    known = set()
    for r in rows:
        known.add(r["video_id"])
        hit = index.get(r["video_id"])
        path_ok = r["file_path"] and os.path.exists(r["file_path"])
        if hit and not path_ok:
            conn.execute("UPDATE videos SET file_path=?, file_size=?, status='downloaded' WHERE id=?",
                         (hit["path"], os.path.getsize(hit["path"]), r["id"]))
            counts["restored" if r["status"] == "missing" else "relinked"] += 1
        elif hit and r["status"] != "downloaded":
            conn.execute("UPDATE videos SET status='downloaded' WHERE id=?", (r["id"],))
            counts["restored"] += 1
        elif not hit and not path_ok and r["status"] == "downloaded":
            conn.execute("UPDATE videos SET status='missing' WHERE id=?", (r["id"],))
            counts["missing"] += 1
    for vid, hit in index.items():
        if vid in known:
            continue
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(os.path.getmtime(hit["path"])))
        conn.execute(
            """INSERT OR IGNORE INTO videos (video_id, extractor, video_name, channel_name,
                   file_size, file_path, status, source, downloaded_at)
               VALUES (?, 'youtube', ?, ?, ?, ?, 'downloaded', 'import', ?)""",
            (vid, hit["title"], hit["channel"], os.path.getsize(hit["path"]), hit["path"], mtime))
        counts["imported"] += 1
    conn.commit()
    return counts


# ------------------------------------------------------------------ stats ----
def stats(conn):
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(status='downloaded'), 0) AS downloaded,
                  COALESCE(SUM(status='missing'), 0)    AS missing,
                  COALESCE(SUM(status='pruned'), 0)     AS pruned,
                  COALESCE(SUM(CASE WHEN status='downloaded' THEN file_size END), 0) AS bytes
           FROM videos""").fetchone()
    return dict(row)


def run_downloads(conn, run_id):
    return conn.execute("SELECT COUNT(*) FROM videos WHERE run_id=? AND source='download'",
                        (run_id,)).fetchone()[0]


def latest_videos(conn, limit=5, run_id=None):
    if run_id is not None:
        cur = conn.execute("SELECT * FROM videos WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit))
    else:
        cur = conn.execute("SELECT * FROM videos ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in cur]


def channel_names(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT channel_name FROM videos WHERE channel_name IS NOT NULL ORDER BY channel_name COLLATE NOCASE")]


def list_videos(conn, q="", channel="", status="", limit=50, offset=0):
    where, args = [], []
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(video_name LIKE ? ESCAPE '\\' OR channel_name LIKE ? ESCAPE '\\')")
        args += [f"%{esc}%", f"%{esc}%"]
    if channel:
        where.append("channel_name = ?")
        args.append(channel)
    if status:
        where.append("status = ?")
        args.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM videos {clause}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM videos {clause} ORDER BY downloaded_at DESC, id DESC LIMIT ? OFFSET ?",
        args + [limit, offset]).fetchall()
    return total, [dict(r) for r in rows]


def get_video(conn, row_id):
    row = conn.execute("SELECT * FROM videos WHERE id=?", (row_id,)).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------- runs ----
def start_run(conn, trigger):
    cur = conn.execute("INSERT INTO runs (trigger, result) VALUES (?, 'running')", (trigger,))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id, result, failures=0, note=None):
    new = run_downloads(conn, run_id)
    conn.execute("UPDATE runs SET finished_at=CURRENT_TIMESTAMP, result=?, new_videos=?, failures=?, note=? WHERE id=?",
                 (result, new, failures, note, run_id))
    conn.commit()
    return new


def recent_runs(conn, limit=8):
    return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


def last_run_started_epoch(conn):
    row = conn.execute("SELECT strftime('%s', started_at) FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return int(row[0]) if row and row[0] else None


def close_stale_runs(conn):
    """A container restart can leave a run marked 'running' - close it."""
    conn.execute("UPDATE runs SET result='failed', finished_at=CURRENT_TIMESTAMP, note='interrupted (container restarted)' WHERE result='running'")
    conn.commit()


# ------------------------------------------------------------------ prune ----
def _inside(path, root):
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    return real == real_root or real.startswith(real_root + os.sep)


def prune_oldest(conn, spool, free_target_bytes):
    """Delete the oldest downloaded files (spool only, never the library) until
    `free_target_bytes` are free. Rows stay in the database as 'pruned', so the
    videos are *not* downloaded again. Returns the list of pruned rows."""
    pruned = []
    rows = conn.execute("SELECT * FROM videos WHERE status='downloaded' AND file_path IS NOT NULL "
                        "ORDER BY downloaded_at ASC, id ASC").fetchall()
    for r in rows:
        if shutil.disk_usage(spool).free >= free_target_bytes:
            break
        path = r["file_path"]
        if not _inside(path, spool) or not os.path.isfile(path):
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        conn.execute("UPDATE videos SET status='pruned' WHERE id=?", (r["id"],))
        conn.commit()
        pruned.append(dict(r))
    return pruned


# -------------------------------------------------------------------- CLI ----
def _cli(argv=None):
    ap = argparse.ArgumentParser(prog="python -m ytapp.ytdb")
    ap.add_argument("command", choices=["init", "status", "reconcile", "export-archive"])
    args = ap.parse_args(argv)
    conn = connect()
    init(conn)
    if args.command == "reconcile":
        print(reconcile(conn))
    elif args.command == "export-archive":
        print(f"{export_archive(conn, settings.ARCHIVE)} ids written to {settings.ARCHIVE}")
    elif args.command == "status":
        print(stats(conn))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
