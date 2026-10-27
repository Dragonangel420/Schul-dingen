import os

from ytapp import channels, settings


def test_validate_accepts_urls_and_comments_and_adds_newline():
    clean, errors = channels.validate("# c\r\nhttps://www.youtube.com/@a/videos\r\n\r\n\r\nhttp://x.y/z")
    assert errors == []
    assert clean == "# c\nhttps://www.youtube.com/@a/videos\n\n\nhttp://x.y/z\n"   # blank lines are kept


def test_validate_rejects_options_and_other_schemes():
    _clean, errors = channels.validate("--exec rm -rf /\nftp://x\nhttps://ok.example/a b\njavascript:alert(1)\n")
    assert [n for n, _ in errors] == [1, 2, 3, 4]


def test_validate_rejects_control_characters():
    assert channels.validate("https://a.b/\x07c")[1]


def test_save_writes_backup_and_trailing_newline():
    ok, _ = channels.save_list("channels_1080p.conf", "https://a.example/one")
    assert ok
    ok, _ = channels.save_list("channels_1080p.conf", "https://a.example/two")
    path = os.path.join(settings.CONF_DIR, "channels_1080p.conf")
    assert open(path).read() == "https://a.example/two\n"
    assert open(path + ".bak").read() == "https://a.example/one\n"


def test_save_refuses_unknown_list_names():
    try:
        channels.path_for("../../etc/passwd")
    except KeyError:
        return
    raise AssertionError("path traversal accepted")


def test_invalid_save_does_not_touch_file():
    channels.save_list("channels_1080p.conf", "https://a.example/one")
    ok, errors = channels.save_list("channels_1080p.conf", "--bad")
    assert not ok and errors
    assert channels.read_list("channels_1080p.conf") == "https://a.example/one\n"
