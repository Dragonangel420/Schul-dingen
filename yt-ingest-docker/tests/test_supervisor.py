import os
import sys

from conftest import ROOT
from ytapp import settings, supervisor, util, ytdb

HERE = os.path.dirname(os.path.abspath(__file__))


def setup_fake(monkeypatch, ids, **env):
    shim_script = os.path.join(ROOT, "shim.sh")
    with open(shim_script, "w") as fh:
        fh.write(f"#!/bin/sh\nexec {sys.executable} -m ytapp.shim \"$@\"\n")
    os.chmod(shim_script, 0o755)
    monkeypatch.setattr(settings, "PIPELINE", os.path.join(HERE, "fake_pipeline.sh"))
    monkeypatch.setenv("SHIM", shim_script)
    monkeypatch.setenv("YTDLP_CMD", f"{sys.executable} {os.path.join(HERE, 'fake_ytdlp.py')}")
    monkeypatch.setenv("FAKE_IDS", ids)
    monkeypatch.setenv("PYTHONPATH", os.path.dirname(HERE))
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))


def test_full_cycle_output_status_blocks_and_last_line(monkeypatch, capsys, conn):
    ids = ",".join(f"{c}{c}{c}{c}{c}{c}{c}{c}{c}{c}{c}" for c in "ABCDEFGHIJKL")        # 12 downloads
    setup_fake(monkeypatch, ids, FAKE_DELAY="0.3")
    monkeypatch.setattr(settings, "HOST_DATA_DIR", "/srv/yt-ingest-data")
    supervisor.complete_run(conn, "manual")
    out = capsys.readouterr().out.splitlines()

    assert out[-1] == ("Download folder: /srv/yt-ingest-data/downloads/Automatic   "
                       f"(inside the container: {settings.SPOOL})")                    # very last line
    assert out[-2].startswith("[supervisor] next scheduled run:")
    text = "\n".join(out)
    assert "[cleanup] start strict base-name cleanup" in text                         # bash stdout reached the log + terminal
    assert text.count("[db] recorded youtube") == 12
    assert text.count("DATABASE STATUS") == 2                                          # after download 5 and 10
    import re
    counts = [int(n) for n in re.findall(r"(\d+) new download\(s\)", text)]
    assert len(counts) == 2 and counts[0] >= 5 and counts[1] >= 10                   # blocks appear once 5 / 10 are reached
    log = open(settings.LOG_FILE).read()
    assert "[db] recorded youtube" in log and "DATABASE STATUS" in log and "Download folder:" in log
    assert ytdb.stats(conn)["downloaded"] == 12
    assert conn.execute("SELECT result, new_videos FROM runs").fetchone()[:] == ("ok", 12)


def test_status_block_is_held_for_configured_seconds(monkeypatch, capsys, conn):
    import time
    ids = ",".join(f"{c}{c}{c}{c}{c}{c}{c}{c}{c}{c}{c}" for c in "ABCDEF")
    setup_fake(monkeypatch, ids, FAKE_DELAY="0.3")
    monkeypatch.setattr(settings, "STATUS_HOLD_SECONDS", 2)
    t = time.time()
    supervisor.complete_run(conn, "manual")
    assert time.time() - t >= 2


def test_second_run_downloads_nothing_new_but_still_prints_path(monkeypatch, capsys, conn):
    setup_fake(monkeypatch, "AAAAAAAAAAA")
    supervisor.complete_run(conn, "manual")
    capsys.readouterr()
    monkeypatch.setenv("FAKE_IDS", "")
    supervisor.complete_run(conn, "schedule")
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith("Download folder: ")
    assert [r[0] for r in conn.execute("SELECT new_videos FROM runs ORDER BY id")] == [1, 0]


def test_run_is_skipped_when_disk_is_full_policy_pause(monkeypatch, capsys, conn):
    setup_fake(monkeypatch, "AAAAAAAAAAA")
    monkeypatch.setattr(settings, "MIN_FREE_GB", 10 ** 9)
    supervisor.complete_run(conn, "schedule")
    out = capsys.readouterr().out
    assert "DISK FULL" in out and "Run skipped" in out
    assert ytdb.stats(conn)["total"] == 0
    assert conn.execute("SELECT result FROM runs").fetchone()[0] == "storage_full"
    assert out.splitlines()[-1].startswith("Download folder: ")


def test_policy_prune_deletes_oldest_and_keeps_ids(monkeypatch, capsys, conn):
    import shutil
    from conftest import make_video
    setup_fake(monkeypatch, "")
    p = make_video("Old [oooooooooo1].mkv", size=100)
    ytdb.record_video(conn, "oooooooooo1", "Old", path=p, size=100)
    real_du = shutil.disk_usage

    def fake_du(path):          # the disk counts as full as long as the old file exists
        u = real_du(path)
        return type("U", (), {"free": 1 if os.path.exists(p) else u.free, "total": u.total, "used": u.used})()

    monkeypatch.setattr(shutil, "disk_usage", fake_du)
    monkeypatch.setattr(settings, "STORAGE_POLICY", "prune")
    supervisor.complete_run(conn, "schedule")
    out = capsys.readouterr().out
    assert 'pruned "Old"' in out
    assert ytdb.get_video(conn, 1)["status"] == "pruned" and not os.path.exists(p)
    assert ("youtube", "oooooooooo1") in ytdb.known_ids(conn)
    assert conn.execute("SELECT result FROM runs").fetchone()[0] == "ok"


def test_storage_flag_aborts_running_pipeline(monkeypatch, capsys, conn):
    setup_fake(monkeypatch, "", FAKE_PIPELINE_DELAY="30")
    import threading, time

    def raise_flag():
        time.sleep(2)
        open(settings.STORAGE_FLAG, "w").write("{}")

    threading.Thread(target=raise_flag).start()
    t = time.time()
    supervisor.complete_run(conn, "manual")
    assert time.time() - t < 20
    out = capsys.readouterr().out
    assert "storage-full flag detected" in out
    assert conn.execute("SELECT result FROM runs").fetchone()[0] == "storage_full"


def test_schedule_math():
    import datetime as dt
    n = dt.datetime(2026, 10, 11, 12, 0)
    assert supervisor.next_run_after(n, "03:00") == dt.datetime(2026, 10, 12, 3, 0)
    assert supervisor.next_run_after(n, "23:30") == dt.datetime(2026, 10, 11, 23, 30)
    assert supervisor.parse_schedule("garbage") == (3, 0)


def test_run_on_start_modes(monkeypatch, conn):
    monkeypatch.setattr(settings, "RUN_ON_START", "auto")
    assert supervisor.should_run_on_start(conn) is True          # never ran
    rid = ytdb.start_run(conn, "x")
    ytdb.finish_run(conn, rid, "ok")
    assert supervisor.should_run_on_start(conn) is False         # ran just now
    monkeypatch.setattr(settings, "RUN_ON_START", "always")
    assert supervisor.should_run_on_start(conn) is True
    monkeypatch.setattr(settings, "RUN_ON_START", "never")
    assert supervisor.should_run_on_start(conn) is False


def test_seed_defaults_does_not_overwrite_user_edits():
    os.makedirs(settings.DEFAULTS_DIR, exist_ok=True)
    open(os.path.join(settings.DEFAULTS_DIR, "channels_1080p.conf"), "w").write("default\n")
    open(os.path.join(settings.DEFAULTS_DIR, "channels_2160p.conf"), "w").write("default\n")
    open(os.path.join(settings.CONF_DIR, "channels_1080p.conf"), "w").write("mine\n")
    supervisor.setup_dirs()
    assert open(os.path.join(settings.CONF_DIR, "channels_1080p.conf")).read() == "mine\n"
    assert open(os.path.join(settings.CONF_DIR, "channels_2160p.conf")).read() == "default\n"


def test_log_rotation_keeps_n(monkeypatch):
    monkeypatch.setattr(settings, "LOG_KEEP", 3)
    for i in range(6):
        open(settings.LOG_FILE, "w").write(f"gen {i}\n")
        os.utime(settings.LOG_FILE, (0, 0))      # pretend it is from an old day
        supervisor.rotate_logs()
    names = sorted(os.listdir(settings.LOG_DIR))
    assert names == ["ingest.log.1", "ingest.log.2", "ingest.log.3"]
    assert open(settings.LOG_FILE + ".1").read() == "gen 5\n"


def test_setup_dirs_creates_targets_behind_dangling_symlinks(monkeypatch, tmp_path):
    """Fresh empty bind mount on /data: the symlinks from the image point at folders that do not exist yet."""
    data = tmp_path / "data"
    data.mkdir()
    links = {}
    for name, target in {"SPOOL": "downloads/Automatic", "CONF_DIR": "config", "LOG_DIR": "logs", "STATE_DIR": "db"}.items():
        link = tmp_path / f"link_{name}"
        link.symlink_to(data / target)             # dangling
        links[name] = str(link)
        monkeypatch.setattr(settings, name, str(link))
    monkeypatch.setattr(settings, "RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(settings, "LIBRARY", str(tmp_path / "lib"))
    monkeypatch.setattr(settings, "DEFAULTS_DIR", str(tmp_path / "nodefaults"))
    supervisor.setup_dirs()
    for t in ("downloads/Automatic", "config", "logs", "db"):
        assert (data / t).is_dir()
    util.log_line("works", str(data / "logs" / "x.log"))
    assert ytdb.connect(str(tmp_path / "link_STATE_DIR" / "t.db")) is not None


def test_final_path_line_variants(monkeypatch):
    monkeypatch.setattr(settings, "HOST_DATA_DIR", "")
    monkeypatch.setattr(settings, "HOST_DOWNLOAD_DIR", "")
    assert "/videos)" in supervisor.final_path_line()
    monkeypatch.setattr(settings, "HOST_DATA_DIR", "/srv/yt/")
    assert supervisor.final_path_line().startswith("Download folder: /srv/yt/downloads/Automatic   (")
    monkeypatch.setattr(settings, "HOST_DOWNLOAD_DIR", "/mnt/storage/youtube")
    assert supervisor.final_path_line().startswith("Download folder: /mnt/storage/youtube/Automatic   (")
