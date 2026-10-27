#!/usr/bin/env python3
"""Stand-in for the real yt-dlp: emits progress lines and 'finished' markers like
`--print after_move:@@YTDB@@...` does. Behaviour is steered through environment variables."""
import json
import os
import sys
import time

args = sys.argv[1:]
spool = os.environ["YT_SPOOL"]
print("[youtube] Extracting URL: " + args[-1] if args else "no url", flush=True)
for vid in os.environ.get("FAKE_IDS", "").split(","):
    if not vid:
        continue
    path = os.path.join(spool, "Chan", f"Title {vid} [{vid}].mkv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    print(f"[download]  50.0% of 1.00MiB at 1.00MiB/s ETA 00:01", flush=True)
    time.sleep(float(os.environ.get("FAKE_DELAY", "0")))
    open(path, "wb").write(b"v" * 2048)
    rec = {"id": vid, "extractor_key": "Youtube", "title": f"Title {vid}", "channel": "Chan",
           "duration": 90, "webpage_url": f"https://youtu.be/{vid}", "filepath": path}
    print("@@YTDB@@" + json.dumps(rec), flush=True)
if os.environ.get("FAKE_HANG"):
    time.sleep(60)
sys.stderr.write("ERROR: something on stderr\n")
sys.exit(int(os.environ.get("FAKE_RC", "0")))
