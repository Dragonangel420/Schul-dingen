import json
import os

import pytest

from conftest import make_video
from ytapp import settings, util, ytdb
from ytapp.webui import create_app


@pytest.fixture
def client():
    return create_app().test_client()


def test_pages_render(client, conn):
    for url in ("/", "/live", "/channels", "/channels?list=channels_2160p.conf", "/videos", "/health", "/api/status", "/api/log"):
        assert client.get(url).status_code == 200, url
    assert client.get("/channels?list=nope").status_code == 404


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_channel_edit_roundtrip_and_validation(client):
    r = client.post("/channels", data={"list": "channels_1080p.conf", "content": "https://a.example/v"})
    assert r.status_code == 302
    assert open(os.path.join(settings.CONF_DIR, "channels_1080p.conf")).read() == "https://a.example/v\n"
    bad = client.post("/channels", data={"list": "channels_1080p.conf", "content": "--exec evil"})
    assert bad.status_code == 422 and b"Not a http(s) URL" in bad.data
    assert open(os.path.join(settings.CONF_DIR, "channels_1080p.conf")).read() == "https://a.example/v\n"
    assert client.post("/channels", data={"list": "../x", "content": "x"}).status_code == 404


def test_cross_site_post_is_blocked(client):
    r = client.post("/run-now", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post("/channels", data={"list": "channels_1080p.conf", "content": "https://x.example"},
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_run_now_creates_trigger_unless_running(client):
    assert client.post("/run-now").status_code == 200
    assert os.path.exists(settings.TRIGGER_FILE)
    os.remove(settings.TRIGGER_FILE)
    util.write_json(settings.STATE_FILE, {"running": True})
    assert client.post("/run-now").status_code == 409


def test_video_list_search_and_play_with_range(client, conn):
    path = make_video("Hello [abcdefghijk].mkv", size=2000)
    ytdb.record_video(conn, "abcdefghijk", "Hello", "Chan", 61, 2000, path)
    assert b"Hello" in client.get("/videos").data
    assert b"Hello" in client.get("/videos?q=hel&channel=Chan").data
    assert b"No video matches" in client.get("/videos?q=zzz").data
    assert client.get("/videos/1").status_code == 200
    r = client.get("/videos/1/file", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100 and r.headers["Content-Type"].startswith("video/x-matroska")


def test_play_follows_rename_done_by_pipeline(client, conn):
    path = make_video("Clip [abcdefghijk].mkv", size=300)
    ytdb.record_video(conn, "abcdefghijk", "Clip", path=path)
    os.rename(path, os.path.join(os.path.dirname(path), "7 - Clip [abcdefghijk].mkv"))
    assert client.get("/videos/1/file").status_code == 200


def test_file_outside_download_folders_is_never_served(client, conn):
    ytdb.record_video(conn, "abcdefghijk", "Evil", path="/etc/passwd")
    assert client.get("/videos/1/file").status_code == 404
    link = os.path.join(settings.SPOOL, "link [zzzzzzzzzzz].mkv")
    os.symlink("/etc/passwd", link)
    ytdb.record_video(conn, "zzzzzzzzzzz", "Link", path=link)
    assert client.get("/videos/2/file").status_code == 404


def test_missing_file_page(client, conn):
    ytdb.record_video(conn, "abcdefghijk", "Gone", path="/nonexistent/x.mkv")
    r = client.get("/videos/1")
    assert r.status_code == 200 and b"not in the download folder" in r.data
    assert client.get("/videos/999").status_code == 404


def test_storage_alert_visible_when_flagged(client):
    open(settings.STORAGE_FLAG, "w").write("{}")
    assert json.loads(client.get("/api/status").data)["storage_low"] is True
