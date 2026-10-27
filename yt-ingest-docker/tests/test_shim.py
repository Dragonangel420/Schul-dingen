import os
import sys

from conftest import ROOT
from ytapp import settings, shim, ytdb

FAKE = f"{sys.executable} {os.path.join(os.path.dirname(__file__), 'fake_ytdlp.py')}"


def run_shim(monkeypatch, capsys, args, **env):
    monkeypatch.setenv("YTDLP_CMD", FAKE)
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    rc = shim.main(args)
    return rc, capsys.readouterr().out


def test_option_value():
    assert shim.option_value(["--a", "1", "--download-archive", "x.txt"], "--download-archive") == "x.txt"
    assert shim.option_value(["--download-archive=y.txt"], "--download-archive") == "y.txt"
    assert shim.option_value(["--a"], "--download-archive") is None


def test_shim_records_downloads_and_rebuilds_archive_from_db(monkeypatch, capsys, conn):
    ytdb.record_video(conn, "KNOWNKNOWN1", "Already known")       # in DB only, not in archive file
    archive = os.path.join(settings.CONF_DIR, "archive.txt")
    open(archive, "w").write("youtube STALEFROMOLD\n")           # stale content must be replaced
    rc, out = run_shim(monkeypatch, capsys, ["--download-archive", archive, "http://x"],
                       FAKE_IDS="AAAAAAAAAAA,BBBBBBBBBBB", YT_RUN_ID="7")
    assert rc == 0
    assert open(archive).read().splitlines() == ["youtube KNOWNKNOWN1"]       # written from the database
    assert out.count("[db] recorded youtube") == 2 and "@@YTDB@@" not in out
    assert "[download]  50.0%" in out and "ERROR: something on stderr" in out    # yt-dlp output passes through
    assert ytdb.run_downloads(conn, 7) == 2
    row = ytdb.get_video(conn, 2)
    assert row["video_name"] == "Title AAAAAAAAAAA" and row["file_size"] == 2048 and row["channel_name"] == "Chan"
    # the next call knows all three ids
    run_shim(monkeypatch, capsys, ["--download-archive", archive, "http://x"])
    assert len(open(archive).read().splitlines()) == 3


def test_shim_exit_code_is_passed_on(monkeypatch, capsys):
    rc, _ = run_shim(monkeypatch, capsys, ["http://x"], FAKE_RC="1")
    assert rc == 1


def test_shim_refuses_when_disk_nearly_full(monkeypatch, capsys):
    monkeypatch.setattr(settings, "MIN_FREE_GB", 10 ** 9)
    rc, out = run_shim(monkeypatch, capsys, ["http://x"], FAKE_IDS="AAAAAAAAAAA")
    assert rc == shim.EXIT_STORAGE and "[storage] not downloading" in out
    assert os.path.exists(settings.STORAGE_FLAG)
    assert ytdb.stats(ytdb.connect())["total"] == 0


def test_shim_refuses_while_flag_is_set(monkeypatch, capsys):
    open(settings.STORAGE_FLAG, "w").write("{}")
    rc, out = run_shim(monkeypatch, capsys, ["http://x"], FAKE_IDS="AAAAAAAAAAA")
    assert rc == shim.EXIT_STORAGE and "flag is set" in out


def test_watchdog_stops_download_when_space_runs_out(monkeypatch, capsys):
    # free space drops below the hard limit after startup -> child is stopped, flag written
    state = {"calls": 0}
    real = shim.util.free_bytes

    def fake_free(path=None):
        state["calls"] += 1
        return real(path) if state["calls"] <= 1 else 1   # 1 byte left from the 2nd check on

    monkeypatch.setattr(shim.util, "free_bytes", fake_free)
    monkeypatch.setattr(shim.threading.Event, "wait", lambda self, t=None: self.is_set() and True or False)
    rc, out = run_shim(monkeypatch, capsys, ["http://x"], FAKE_HANG="1")
    assert rc == shim.EXIT_STORAGE and "stopping yt-dlp" in out
    assert os.path.exists(settings.STORAGE_FLAG)


def test_handle_line_ignores_garbage_marker(conn):
    out = shim.handle_line(conn, shim.MARKER + "{not json", None)
    assert "could not parse" in out
    assert shim.handle_line(conn, "plain line\n", None) == "plain line\n"
