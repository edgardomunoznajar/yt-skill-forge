#!/usr/bin/env python3
"""Aggregate watch history into a per-creator table for the agent to judge.

Deliberately does no scoring judgement of its own. It reports the four numbers
that separate "a creator I learn from" from "a creator I killed an evening on":

  videos       distinct videos watched -- breadth of engagement
  events       total watch events -- rewatches count
  span_days    first to last watch -- sustained interest vs one binge
  last_watched recency -- is this still live

A binge of 30 videos in one weekend and 30 videos over two years look identical
on a plain count and very different on span. The agent reads both.

Browser-sourced rows have no channel, so this resolves them through yt-dlp one
video at a time and caches the answer -- resolution is the slow part and must
never be repeated across runs.
"""
import argparse
import json
import os
import subprocess
import sys

import ytdlp
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "history.jsonl")
CACHE = os.path.join(ROOT, "data", "video_channels.json")
OUT = os.path.join(ROOT, "data", "creators.json")
YTDLP = ytdlp.find()


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            return json.load(f)
    return {}


def resolve(ids, cache, limit, batch=20):
    """Fill channel names for video ids, caching as we go.

    Batched: one yt-dlp process per 20 ids rather than per id, which is the
    difference between minutes and an hour on a real history. Metadata is
    printed straight from the info dict, so --ignore-no-formats-error is
    required -- without it yt-dlp still runs format selection and aborts on
    age-restricted, member-only, and region-locked videos, which silently
    loses most of a typical history.
    """
    todo = [i for i in ids if i not in cache][:limit]
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        cmd = [YTDLP, "--skip-download", "--ignore-no-formats-error",
               "--ignore-errors", "--no-warnings", "--socket-timeout", "20",
               "--sleep-requests", "1",
               "--print", "%(id)s\t%(channel)s\t%(channel_url)s"]
        cmd += [f"https://www.youtube.com/watch?v={i}" for i in chunk]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * batch)
            out = r.stdout
        except subprocess.TimeoutExpired:
            out = ""
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            vid, name, url = parts
            cache[vid] = {"channel": None if name in ("", "NA") else name,
                          "channel_url": url if url.startswith("http") else None}
        # cache misses as null too, so dead videos are not retried forever
        for vid in chunk:
            cache.setdefault(vid, {"channel": None, "channel_url": None})
        with open(CACHE, "w") as f:
            json.dump(cache, f, indent=1)
        got = sum(1 for i in chunk if cache[i]["channel"])
        print(f"  resolved {start + len(chunk)}/{len(todo)} "
              f"(+{got} named this batch)", flush=True)
    return cache


def parse_day(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-videos", type=int, default=3,
                    help="drop creators below this many distinct videos")
    ap.add_argument("--resolve-limit", type=int, default=150,
                    help="cap yt-dlp lookups per run (~14s each under YouTube's "
                         "throttle); re-run to continue")
    ap.add_argument("--no-resolve", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(HIST, encoding="utf-8")]
    unknown = sorted({r["video_id"] for r in rows if not r["channel"]})
    cache = load_cache()
    if unknown and not a.no_resolve:
        pending = [i for i in unknown if i not in cache]
        print(f"resolving {min(len(pending), a.resolve_limit)} of {len(pending)} "
              f"unattributed videos via yt-dlp", flush=True)
        cache = resolve(unknown, cache, a.resolve_limit)
        with open(CACHE, "w") as f:
            json.dump(cache, f, indent=1)

    agg = defaultdict(lambda: {"videos": set(), "events": 0, "days": [],
                               "channel_url": None, "titles": []})
    for r in rows:
        ch = r["channel"] or (cache.get(r["video_id"]) or {}).get("channel")
        if not ch:
            continue
        url = r["channel_url"] or (cache.get(r["video_id"]) or {}).get("channel_url")
        e = agg[ch]
        e["videos"].add(r["video_id"])
        e["events"] += 1
        e["channel_url"] = e["channel_url"] or url
        d = parse_day(r["watched_at"])
        if d:
            e["days"].append(d)
        if len(e["titles"]) < 25 and r["title"] not in e["titles"]:
            e["titles"].append(r["title"])

    out = []
    for ch, e in agg.items():
        if len(e["videos"]) < a.min_videos:
            continue
        days = sorted(e["days"])
        out.append({
            "channel": ch,
            "channel_url": e["channel_url"],
            "videos": len(e["videos"]),
            "events": e["events"],
            "span_days": (days[-1] - days[0]).days if len(days) > 1 else 0,
            "first_watched": days[0].isoformat() if days else None,
            "last_watched": days[-1].isoformat() if days else None,
            "sample_titles": e["titles"],
        })
    out.sort(key=lambda r: (-r["videos"], -r["events"]))

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)

    print(f"\n{'videos':>6} {'events':>6} {'span':>6}  {'last':<11} channel")
    for r in out[:40]:
        print(f"{r['videos']:>6} {r['events']:>6} {r['span_days']:>6}  "
              f"{r['last_watched'] or '-':<11} {r['channel']}")
    dropped = len(agg) - len(out)
    print(f"\n{len(out)} creators >= {a.min_videos} videos ({dropped} below cut)")
    print(f"-> {a.out}")


if __name__ == "__main__":
    sys.exit(main())
