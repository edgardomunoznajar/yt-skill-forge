#!/usr/bin/env python3
"""Harvest YouTube watch history into one normalized JSONL stream.

Three sources, in descending order of quality:

  takeout  Google Takeout watch-history.json. The only complete source --
           years deep, every device, and it names the channel directly.
  chrome   Chrome's History sqlite. Capped at ~90 days, this machine only,
           and titles carry no channel, so channels must be resolved later.
  firefox  Same shape, same limits, via places.sqlite.

Browser DBs are copied before reading: a running browser holds a write lock,
and opening the live file can block or see a partial WAL.

Output rows: {video_id, title, channel, channel_url, watched_at, source}
Channel is null for browser rows -- rank_creators.py fills it in.
"""
import argparse
import glob
import html
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "history.jsonl")

VID = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")
TAKEOUT_GLOBS = [
    "~/Takeout/YouTube and YouTube Music/history/watch-history.json",
    "~/Downloads/Takeout/YouTube and YouTube Music/history/watch-history.json",
    "~/Downloads/**/watch-history.json",
    "~/Downloads/**/watch-history.html",
    "~/Downloads/takeout-*.zip",
    "~/Downloads/takeout-*.tgz",
    "~/**/watch-history.json",
    "~/**/watch-history.html",
]


def find_takeout():
    for g in TAKEOUT_GLOBS:
        hits = glob.glob(os.path.expanduser(g), recursive=True)
        if hits:
            return sorted(hits, key=os.path.getmtime)[-1]
    return None


def read_takeout(path):
    """Return (leaf_name, bytes) for the watch-history file in a Takeout export.

    Accepts the .zip or .tgz exactly as Google delivers it, as well as an
    already-extracted watch-history.json/.html. Takeout splits large exports
    across numbered archives and localizes the folder name ("YouTube y YouTube
    Music"), so members are matched on the leaf filename rather than a path.

    Both export formats are supported. JSON is the better one and what the
    docs recommend, but the format toggle is easy to miss in the Takeout UI and
    re-requesting costs hours, so HTML is parsed rather than rejected.
    """
    if path.endswith((".zip", ".tgz", ".tar.gz")):
        opener = (zipfile.ZipFile(path) if path.endswith(".zip")
                  else tarfile.open(path, "r:gz"))
        with opener as ar:
            names = (ar.namelist() if isinstance(ar, zipfile.ZipFile)
                     else ar.getnames())
            hits = ([n for n in names if n.endswith("/watch-history.json")] or
                    [n for n in names if n.endswith("/watch-history.html")])
            if not hits:
                raise SystemExit(
                    f"{path} contains no watch-history.json or .html.\n"
                    "Re-request with only 'YouTube and YouTube Music' -> history.")
            raw = (ar.read(hits[0]) if isinstance(ar, zipfile.ZipFile)
                   else ar.extractfile(hits[0]).read())
        return hits[0], raw
    with open(path, "rb") as f:
        return path, f.read()


# HTML export: one "outer-cell" div per event. Within it the content cell holds
# "Watched <a href=video>title</a><br><a href=channel>name</a><br>timestamp".
# Parsed with regex rather than an HTML parser because the file runs to tens of
# megabytes of uniform machine-generated markup, and html.parser on 45MB is
# minutes rather than seconds.
CELL = re.compile(r'<div class="outer-cell', re.I)
ANCHOR = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
# Takeout renders the timestamp in the account's own locale, so the layout is
# not fixed. Two forms cover what Google emits: day-first 24-hour ("10 Aug
# 2026, 14:37:27 ACST", en-AU/en-GB) and month-first 12-hour ("Aug 10, 2026,
# 2:37:27 PM ACST", en-US). The trailing zone abbreviation is dropped -- it is
# the user's own zone throughout, and only day resolution is used downstream.
STAMP_DMY = re.compile(
    r'(\d{1,2}) (\w{3,}) (\d{4}),\s*(\d{1,2}):(\d{2}):(\d{2})(?:\s*([AP]M))?')
STAMP_MDY = re.compile(
    r'(\w{3,}) (\d{1,2}), (\d{4}),\s*(\d{1,2}):(\d{2}):(\d{2})(?:\s*([AP]M))?')
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _iso(cell):
    m = STAMP_DMY.search(cell)
    if m:
        day, mon, year, hh, mm, ss, ap = m.groups()
    else:
        m = STAMP_MDY.search(cell)
        if not m:
            return None
        mon, day, year, hh, mm, ss, ap = m.groups()
    month = MONTHS.get(mon[:3].lower())
    if not month:
        return None
    hh = int(hh)
    if ap:  # 12-hour clock; without a meridiem the value is already 24-hour
        hh = hh % 12 + (12 if ap.upper() == "PM" else 0)
    return (f"{year}-{month:02d}-{int(day):02d}T"
            f"{hh:02d}:{int(mm):02d}:{int(ss):02d}")


def from_takeout_html(raw):
    text = raw.decode("utf-8", errors="replace")
    for cell in CELL.split(text)[1:]:
        # the Music section of the export uses the same cell markup
        if "YouTube Music" in cell[:400]:
            continue
        anchors = ANCHOR.findall(cell)
        vid = title = channel = channel_url = None
        for href, label in anchors:
            m = VID.search(href)
            if m and not vid:
                vid, title = m.group(1), re.sub(r"<[^>]+>", "", label).strip()
            elif "/channel/" in href and not channel:
                channel_url = href
                channel = re.sub(r"<[^>]+>", "", label).strip()
        if not vid:
            continue  # removed videos and survey rows carry no watch link
        yield {
            "video_id": vid,
            "title": html.unescape(title or ""),
            "channel": html.unescape(channel) if channel else None,
            "channel_url": channel_url,
            "watched_at": _iso(cell),
            "source": "takeout",
        }


def from_takeout_json(raw):
    for e in json.load(io.BytesIO(raw)):
        url = e.get("titleUrl") or ""
        m = VID.search(url)
        if not m:
            continue  # removed videos, Shorts surveys, and ad rows carry no id
        if e.get("header") not in (None, "YouTube"):
            continue  # skip YouTube Music listening rows
        title = e.get("title", "")
        if title.startswith("Watched "):
            title = title[len("Watched "):]
        subs = e.get("subtitles") or [{}]
        yield {
            "video_id": m.group(1),
            "title": title,
            "channel": subs[0].get("name"),
            "channel_url": subs[0].get("url"),
            "watched_at": e.get("time"),
            "source": "takeout",
        }


def from_takeout(path):
    name, raw = read_takeout(path)
    fmt = "html" if name.endswith(".html") else "json"
    print(f"  format: {fmt} ({len(raw) / 1e6:.1f} MB)", flush=True)
    return from_takeout_html(raw) if fmt == "html" else from_takeout_json(raw)


def from_sqlite(src, table, time_expr, label):
    """Read a browser history DB from a snapshot copy."""
    tmp = tempfile.mkdtemp()
    try:
        db = os.path.join(tmp, "h.db")
        shutil.copy(src, db)
        for side in (src + "-wal", src + "-shm"):
            if os.path.exists(side):
                shutil.copy(side, db + side[len(src):])
        con = sqlite3.connect(db)
        rows = con.execute(
            f"select url, title, {time_expr} from {table} "
            f"where url like '%youtube.com/watch%'"
        ).fetchall()
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for url, title, when in rows:
        m = VID.search(url or "")
        if not m:
            continue
        title = re.sub(r"\s*-\s*YouTube$", "", title or "")
        yield {
            "video_id": m.group(1),
            "title": title,
            "channel": None,
            "channel_url": None,
            # a zero timestamp means typed or bookmarked but never visited
            "watched_at": when if when and not str(when).startswith("1601") else None,
            "source": label,
        }


def from_chrome():
    # Chrome stores microseconds since 1601-01-01
    expr = "datetime(last_visit_time/1000000-11644473600,'unixepoch')"
    for p in sorted(glob.glob(os.path.expanduser("~/.config/google-chrome/*/History"))):
        yield from from_sqlite(p, "urls", expr, "chrome")
    for p in sorted(glob.glob(os.path.expanduser("~/.config/chromium/*/History"))):
        yield from from_sqlite(p, "urls", expr, "chromium")


def from_firefox():
    expr = "datetime(last_visit_date/1000000,'unixepoch')"
    pats = [
        "~/.mozilla/firefox/*/places.sqlite",
        "~/snap/firefox/common/.mozilla/firefox/*/places.sqlite",
    ]
    for pat in pats:
        for p in sorted(glob.glob(os.path.expanduser(pat))):
            yield from from_sqlite(p, "moz_places", expr, "firefox")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--takeout", help="path to watch-history.json (auto-detected if omitted)")
    ap.add_argument("--no-browsers", action="store_true", help="Takeout only")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    rows, seen_src = [], {}
    tk = a.takeout or find_takeout()
    if tk:
        print(f"takeout: {tk}", flush=True)
        rows += list(from_takeout(tk))
    else:
        print("takeout: not found", flush=True)
    if not a.no_browsers:
        rows += list(from_chrome())
        rows += list(from_firefox())

    for r in rows:
        seen_src[r["source"]] = seen_src.get(r["source"], 0) + 1

    # keep every watch event: repeat views are the strongest interest signal
    rows.sort(key=lambda r: (r["watched_at"] or "", r["video_id"]))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    uniq = len({r["video_id"] for r in rows})
    named = len({r["video_id"] for r in rows if r["channel"]})
    print(f"events={len(rows)} unique_videos={uniq} with_channel={named}")
    print("by source: " + ", ".join(f"{k}={v}" for k, v in sorted(seen_src.items())))
    print(f"-> {a.out}")
    if not tk:
        print("\nNOTE: browser history only (~90 days, this machine). For the full\n"
              "history request a Takeout export: https://takeout.google.com/ ->\n"
              "'YouTube and YouTube Music' -> history only.")


if __name__ == "__main__":
    sys.exit(main())
