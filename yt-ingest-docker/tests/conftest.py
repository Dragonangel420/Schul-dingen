"""Test environment: every path points into a temp dir (set before ytapp is imported)."""
import os
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix="ytingest-test-")
for name, sub in {"YT_SPOOL": "spool", "YT_LIBRARY": "library", "YT_CONF_DIR": "conf", "YT_LOG_DIR": "log",
                  "YT_STATE_DIR": "state", "YT_RUN_DIR": "run", "YT_DEFAULTS_DIR": "defaults"}.items():
    os.environ[name] = os.path.join(ROOT, sub)
os.environ.update({"MIN_FREE_GB": "0.001", "STATUS_EVERY": "5", "STATUS_HOLD_SECONDS": "1",
                   "YTDLP_AUTO_UPDATE": "0", "TZ": "UTC"})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil  # noqa: E402
import pytest  # noqa: E402

from ytapp import settings, ytdb  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env():
    for d in (settings.SPOOL, settings.LIBRARY, settings.CONF_DIR, settings.LOG_DIR,
              settings.STATE_DIR, settings.RUN_DIR):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    yield


@pytest.fixture
def conn():
    c = ytdb.connect()
    ytdb.init(c)
    yield c
    c.close()


def make_video(name, channel="Chan", size=1000, root=None):
    root = root or settings.SPOOL
    d = os.path.join(root, channel)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path
