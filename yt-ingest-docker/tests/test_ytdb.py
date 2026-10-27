import os

from conftest import make_video
from ytapp import settings, ytdb


def test_record_is_idempotent_and_new_flag(conn):
    assert ytdb.record_video(conn, "AAAAAAAAAAA", "T1", "C", 60, 10, "/x", "u") is True
    assert ytdb.record_video(conn, "AAAAAAAAAAA", "T1 renamed", "C", 60, 10, "/x", "u") is False
    assert ytdb.stats(conn)["total"] == 1
    assert ytdb.get_video(conn, 1)["video_name"] == "T1 renamed"


def test_export_archive_from_db(conn, tmp_path):
    ytdb.record_video(conn, "AAAAAAAAAAA", "T1")
    ytdb.record_video(conn, "BBBBBBBBBBB", "T2", extractor="youtube")
    archive = str(tmp_path / "archive.txt")
    assert ytdb.export_archive(conn, archive) == 2
    assert open(archive).read().splitlines() == ["youtube AAAAAAAAAAA", "youtube BBBBBBBBBBB"]


def test_reconcile_imports_relinks_and_marks_missing(conn):
    p = make_video("Some title [abcdefghijk].mkv")
    make_video("Partial [zzzzzzzzzzz].f137.mp4.part")     # partial files are ignored
    make_video("Not video [qqqqqqqqqqq].json")
    c = ytdb.reconcile(conn)
    assert c["imported"] == 1
    row = ytdb.get_video(conn, 1)
    assert row["video_name"] == "Some title" and row["channel_name"] == "Chan" and row["source"] == "import"

    new = os.path.join(os.path.dirname(p), "42 - Some title [abcdefghijk].mkv")   # dashcam rename
    os.rename(p, new)
    assert ytdb.reconcile(conn)["relinked"] == 1
    assert ytdb.get_video(conn, 1)["file_path"] == new

    os.remove(new)
    assert ytdb.reconcile(conn)["missing"] == 1
    assert ytdb.get_video(conn, 1)["status"] == "missing"
    assert ("youtube", "abcdefghijk") in ytdb.known_ids(conn)       # still protected from re-download

    make_video("Some title [abcdefghijk].mkv")
    assert ytdb.reconcile(conn)["restored"] == 1
    assert ytdb.get_video(conn, 1)["status"] == "downloaded"


def test_search_escapes_wildcards(conn):
    ytdb.record_video(conn, "AAAAAAAAAAA", "100% real")
    ytdb.record_video(conn, "BBBBBBBBBBB", "plain")
    total, rows = ytdb.list_videos(conn, q="%")
    assert total == 1 and rows[0]["video_name"] == "100% real"
    assert ytdb.list_videos(conn, q="'; DROP TABLE videos; --")[0] == 0
    assert ytdb.stats(conn)["total"] == 2


def test_prune_keeps_ids_and_never_touches_library(conn):
    spool_file = make_video("Old [aaaaaaaaaaa].mkv", size=500)
    lib_file = make_video("Lib [bbbbbbbbbbb].mkv", root=settings.LIBRARY, size=500)
    ytdb.record_video(conn, "aaaaaaaaaaa", "Old", path=spool_file, size=500)
    ytdb.record_video(conn, "bbbbbbbbbbb", "Lib", path=lib_file, size=500)
    pruned = ytdb.prune_oldest(conn, settings.SPOOL, 10 ** 18)   # impossible target: prune everything allowed
    assert [p["video_id"] for p in pruned] == ["aaaaaaaaaaa"]
    assert not os.path.exists(spool_file) and os.path.exists(lib_file)
    assert ytdb.get_video(conn, 1)["status"] == "pruned"
    assert ("youtube", "aaaaaaaaaaa") in ytdb.known_ids(conn)


def test_runs_and_counts(conn):
    rid = ytdb.start_run(conn, "manual")
    ytdb.record_video(conn, "AAAAAAAAAAA", "T", run_id=rid)
    ytdb.record_video(conn, "BBBBBBBBBBB", "T", run_id=rid, source="import")
    assert ytdb.run_downloads(conn, rid) == 1
    assert ytdb.finish_run(conn, rid, "ok") == 1
    assert ytdb.recent_runs(conn)[0]["result"] == "ok"
