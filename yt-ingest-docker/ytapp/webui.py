"""Web UI (Flask + waitress): dashboard, channel list editor, video browser/player.

Browser-based control plane for channel management, run status, and video playback.
The interface binds to the configured WEBUI_HOST and WEBUI_PORT.
"""
import logging
import mimetypes
import os
from urllib.parse import urlparse

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_file, url_for)

from . import __version__, channels, settings, util, ytdb

PAGE_SIZE = 40


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024
    app.jinja_env.filters["size"] = util.human_size
    app.jinja_env.filters["duration"] = util.human_duration

    def db():
        conn = ytdb.connect()
        ytdb.init(conn)
        return conn

    # ------------------------------------------------------------ security --
    @app.before_request
    def same_origin_for_posts():
        if request.method == "POST":
            origin = request.headers.get("Origin") or request.headers.get("Referer")
            if origin and urlparse(origin).netloc != request.host:
                abort(403)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'")
        return resp

    # ------------------------------------------------------------- helpers --
    def status_data():
        state = util.read_json(settings.STATE_FILE, {}) or {}
        conn = db()
        disk = util.disk_info()
        low = disk["free"] < settings.MIN_FREE_GB * settings.GB
        flagged = os.path.exists(settings.STORAGE_FLAG)
        return {
            "running": bool(state.get("running")),
            "run_id": state.get("run_id"),
            "run_started": state.get("run_started"),
            "next_run": state.get("next_run"),
            "schedule": state.get("schedule", settings.SCHEDULE_TIME),
            "stats": ytdb.stats(conn),
            "run_new": ytdb.run_downloads(conn, state["run_id"]) if state.get("running") and state.get("run_id") else 0,
            "disk": disk,
            "min_free": settings.MIN_FREE_GB * settings.GB,
            "storage_low": low or flagged,
            "policy": settings.STORAGE_POLICY,
            "runs": ytdb.recent_runs(conn),
        }

    def tail(path, max_lines=300, max_bytes=256 * 1024):
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                fh.seek(max(0, size - max_bytes))
                data = fh.read().decode("utf-8", "replace")
        except OSError:
            return ""
        return "\n".join(data.splitlines()[-max_lines:])

    def resolve_video_path(row):
        """Return the file path for a DB row (follows renames done by the pipeline)."""
        path = row.get("file_path")
        if not (path and os.path.isfile(path)):
            hit = ytdb.scan_files([settings.SPOOL, settings.LIBRARY]).get(row["video_id"])
            path = hit["path"] if hit else None
        if not path:
            return None
        real = os.path.realpath(path)
        for root in (settings.SPOOL, settings.LIBRARY):
            r = os.path.realpath(root)
            if real.startswith(r + os.sep):
                return real
        return None

    # -------------------------------------------------------------- routes --
    @app.get("/health")
    def health():
        db().execute("SELECT 1")
        return jsonify(ok=True, version=__version__)

    @app.get("/")
    def overview():
        return render_template("overview.html", s=status_data(), page="overview",
                               host_path=settings.host_download_path(), version=__version__)

    @app.get("/live")
    def live_page():
        return render_template("live.html", page="live", host_path=settings.host_download_path())

    @app.get("/api/status")
    def api_status():
        return jsonify(status_data())

    @app.get("/api/log")
    def api_log():
        return jsonify(text=tail(settings.LOG_FILE, int(request.args.get("lines", 300))))

    @app.post("/run-now")
    def run_now():
        state = util.read_json(settings.STATE_FILE, {}) or {}
        if state.get("running"):
            return jsonify(ok=False, message="A run is already in progress."), 409
        os.makedirs(settings.RUN_DIR, exist_ok=True)
        with open(settings.TRIGGER_FILE, "w") as fh:
            fh.write(util.ts())
        return jsonify(ok=True, message="Run requested. It starts within a few seconds.")

    @app.route("/channels", methods=["GET", "POST"])
    def channels_page():
        name = request.values.get("list", settings.CHANNEL_LISTS[0])
        if name not in settings.CHANNEL_LISTS:
            abort(404)
        errors, saved, text = [], request.args.get("saved") == "1", None
        if request.method == "POST":
            text = request.form.get("content", "")
            ok, errors = channels.save_list(name, text)
            if ok:
                return redirect(url_for("channels_page", list=name, saved=1))
        if text is None:
            text = channels.read_list(name)
        lists = [{"name": n, "desc": channels.DESCRIPTIONS[n],
                  "count": channels.count_urls(channels.read_list(n))} for n in settings.CHANNEL_LISTS]
        return render_template("channels.html", page="channels", lists=lists, current=name,
                               text=text, errors=errors, saved=saved), (422 if errors else 200)

    @app.get("/videos")
    def videos_page():
        q = request.args.get("q", "").strip()
        channel = request.args.get("channel", "")
        status = request.args.get("status", "")
        try:
            pg = max(1, int(request.args.get("page", 1)))
        except ValueError:
            pg = 1
        conn = db()
        total, rows = ytdb.list_videos(conn, q, channel, status, PAGE_SIZE, (pg - 1) * PAGE_SIZE)
        pages = max(1, -(-total // PAGE_SIZE))
        return render_template("videos.html", page="videos", rows=rows, total=total, q=q,
                               channel=channel, status=status, channels=ytdb.channel_names(conn),
                               pg=pg, pages=pages)

    @app.get("/videos/<int:row_id>")
    def play_page(row_id):
        row = ytdb.get_video(db(), row_id)
        if not row:
            abort(404)
        playable = resolve_video_path(row) is not None
        return render_template("play.html", page="videos", v=row, playable=playable,
                               host_path=settings.host_download_path())

    @app.get("/videos/<int:row_id>/file")
    def video_file(row_id):
        row = ytdb.get_video(db(), row_id)
        if not row:
            abort(404)
        path = resolve_video_path(row)
        if not path:
            abort(404)
        mime = {".mkv": "video/x-matroska", ".webm": "video/webm"}.get(
            os.path.splitext(path)[1].lower()) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        return send_file(path, mimetype=mime, conditional=True,
                         as_attachment=request.args.get("download") == "1",
                         download_name=os.path.basename(path))

    return app


def main():
    os.umask(0o002)
    from waitress import serve
    logging.getLogger("waitress").setLevel(logging.WARNING)
    serve(create_app(), host=settings.WEBUI_HOST, port=settings.WEBUI_PORT, threads=6,
          ident="yt-ingest")


if __name__ == "__main__":
    main()
