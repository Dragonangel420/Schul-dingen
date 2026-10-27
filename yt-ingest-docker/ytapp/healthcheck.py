"""Docker HEALTHCHECK: exit 0 if the web UI (and therefore the database) answers."""
import sys
import urllib.request

from . import settings

try:
    with urllib.request.urlopen(f"http://127.0.0.1:{settings.WEBUI_PORT}/health", timeout=4) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
