#!/usr/bin/env python3
"""Enumerate a channel's videos into a corpus's meta/videos.jsonl.

Flat playlist only -- one request per page, no per-video lookups, so a
thousand-video channel enumerates in seconds. The raw dump is kept alongside
the reduced file because yt-dlp's flat entries carry fields (availability,
live status, playlist index) that are occasionally needed after the fact and
cannot be recovered without re-enumerating.

Shorts and live streams are separate tabs on a channel; /videos gets the
long-form uploads, which is what carries teachable material.
"""
import argparse
import json
import os
import re
import subprocess
import sys

import ytdlp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP = ytdlp.find()


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "channel"


def normalize(url, tab):
    url = url.rstrip("/")
    if re.search(r"/(videos|shorts|streams|playlists)$", url):
        url = url.rsplit("/", 1)[0]
    return f"{url}/{tab}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel_url", help="e.g. https://www.youtube.com/@handle")
    ap.add_argument("--slug", help="corpus directory name (default: from handle)")
    ap.add_argument("--tab", default="videos", choices=["videos", "shorts", "streams"])
    ap.add_argument("--corpora", default=os.path.join(ROOT, "corpora"))
    a = ap.parse_args()

    url = normalize(a.channel_url, a.tab)
    handle = re.search(r"@([A-Za-z0-9_.-]+)", a.channel_url)
    slug = a.slug or slugify(handle.group(1) if handle else a.channel_url.rsplit("/", 1)[-1])
    meta = os.path.join(a.corpora, slug, "meta")
    os.makedirs(meta, exist_ok=True)
    raw_path = os.path.join(meta, "_raw_videos.jsonl")

    print(f"enumerating {url}\n-> corpora/{slug}/", flush=True)
    cmd = [YTDLP, "--flat-playlist", "--dump-json", "--no-warnings",
           "--ignore-errors", "--sleep-requests", "1", url]
    with open(raw_path, "w", encoding="utf-8") as raw:
        p = subprocess.run(cmd, stdout=raw, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0 and os.path.getsize(raw_path) == 0:
        print(p.stderr.strip()[-800:], file=sys.stderr)
        return 1

    seen, kept = set(), []
    channel = None
    with open(raw_path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            vid = d.get("id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            channel = channel or d.get("channel") or d.get("uploader")
            kept.append({
                "id": vid,
                "title": d.get("title"),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "upload_date": d.get("upload_date"),
                "duration": d.get("duration"),
                "view_count": d.get("view_count"),
            })

    with open(os.path.join(meta, "videos.jsonl"), "w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(meta, "channel.json"), "w", encoding="utf-8") as f:
        json.dump({"slug": slug, "channel": channel, "url": a.channel_url,
                   "tab": a.tab, "video_count": len(kept)}, f, indent=1)

    hours = sum(r["duration"] or 0 for r in kept) / 3600
    print(f"{len(kept)} videos, {hours:.0f}h total runtime")
    print(f"-> {meta}/videos.jsonl")


if __name__ == "__main__":
    sys.exit(main())
