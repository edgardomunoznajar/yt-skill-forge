"""Locate a usable yt-dlp binary.

Resolution order: $YTDLP, the project venv, then PATH. The venv comes before
PATH deliberately -- distro-packaged yt-dlp is routinely years stale (this
machine shipped 2024.04.09), and against YouTube a stale binary does not fail
loudly. It finds the subtitle tracks, reports them, then dies on "Did not get
any data blocks" and writes nothing, which reads downstream as "this channel
has no captions" rather than "your tool is broken."
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, ".venv", "bin", "yt-dlp")

# yt-dlp release IDs are date-shaped (YYYY.MM.DD), so a plain string compare
# orders them; anything older than this predates breaking YouTube changes
MIN_VERSION = "2026.01.01"


def version(path):
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def find(warn=True):
    for cand in (os.environ.get("YTDLP"), VENV, "yt-dlp"):
        if not cand:
            continue
        v = version(cand)
        if not v:
            continue
        if v < MIN_VERSION and warn:
            print(f"WARNING: {cand} is {v}, older than {MIN_VERSION}. Subtitle "
                  f"fetches will silently return nothing.\n"
                  f"  fix: uv pip install --python {ROOT}/.venv/bin/python -U yt-dlp",
                  flush=True)
        return cand
    raise SystemExit("no yt-dlp found; run: uv venv .venv && "
                     "uv pip install --python .venv/bin/python yt-dlp")
