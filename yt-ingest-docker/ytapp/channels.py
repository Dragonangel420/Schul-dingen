"""Reading, validating and saving the channel list files.

The bash pipeline reads each list with `while read -r URL`, which silently
drops a last line that has no trailing newline - so saving always ends the
file with exactly one newline. Only http(s) URLs are accepted: a line such as
`--exec rm ...` would otherwise be handed to yt-dlp as an option.
"""
import os
import re

from . import settings

URL_RE = re.compile(r"^https?://[^\s<>\"'`\\]+$", re.IGNORECASE)
MAX_LINE = 2000
MAX_BYTES = 256 * 1024

DESCRIPTIONS = {
    "channels_1080p.conf": "Downloaded in up to 1080p (AV1 is skipped).",
    "channels_2160p.conf": "Downloaded in up to 4K (AV1 is skipped).",
    "channels_curated.conf": "Only videos from the last 4 weeks, up to 1080p.",
}


def path_for(name):
    if name not in settings.CHANNEL_LISTS:
        raise KeyError(name)
    return os.path.join(settings.CONF_DIR, name)


def read_list(name):
    try:
        with open(path_for(name), encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def validate(text):
    """Return (clean_text, errors). Errors are [(line_number, message)]."""
    errors = []
    if len(text.encode("utf-8")) > MAX_BYTES:
        return text, [(0, f"The list is larger than {MAX_BYTES // 1024} KB.")]
    out = []
    for no, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        line = raw.strip()
        if any(ord(c) < 32 for c in line):
            errors.append((no, "Contains a control character."))
        elif not line or line.startswith("#"):
            out.append(line)
        elif len(line) > MAX_LINE:
            errors.append((no, "Line is too long."))
        elif not URL_RE.match(line):
            errors.append((no, f"Not a http(s) URL: {line[:60]}"))
        else:
            out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n", errors


def count_urls(text):
    return sum(1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))


def save_list(name, text):
    """Validate and save atomically. Returns (ok, errors)."""
    clean, errors = validate(text)
    if errors:
        return False, errors
    path = path_for(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "rb") as src, open(path + ".bak", "wb") as dst:
                dst.write(src.read())
        except OSError:
            pass
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(clean)
    try:
        os.chmod(tmp, 0o664)
    except OSError:
        pass
    os.replace(tmp, path)
    return True, []
