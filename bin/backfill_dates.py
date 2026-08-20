#!/usr/bin/env python3
"""Backfill upload_date and view_count into videos.jsonl from the info JSONs.

`yt-dlp --flat-playlist` is cheap because it does not fetch per-video metadata,
so the videos.jsonl written at enumeration time has a null upload_date. The
per-video info JSONs written during the subtitle fetch do have it. This merges
them back.

Dates matter more than they look. Clustering ranks candidate transcripts by
view count, which is a decent proxy for "most complete treatment" -- but in any
domain with a legislative or version cutoff, the highest-viewed video on a
topic is often the one published *before* the rule changed. Without dates on
the records there is no way to notice, and the resulting skill states the old
rule confidently.
"""
import argparse
import glob
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", help="corpora/<slug> directory")
    a = ap.parse_args()

    corpus = os.path.abspath(a.corpus)
    meta = os.path.join(corpus, "meta")
    vpath = os.path.join(meta, "videos.jsonl")

    info = {}
    for p in glob.glob(os.path.join(meta, "info", "*.info.json")):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("id"):
            info[d["id"]] = d

    rows, filled = [], 0
    with open(vpath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue
            r = json.loads(line)
            d = info.get(r["id"])
            if d:
                for k in ("upload_date", "view_count", "duration"):
                    if r.get(k) is None and d.get(k) is not None:
                        r[k] = d[k]
                        if k == "upload_date":
                            filled += 1
            rows.append(r)

    with open(vpath, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    dated = [r["upload_date"] for r in rows if r.get("upload_date")]
    print(f"{len(rows)} videos, {len(info)} info files, {filled} dates filled")
    if dated:
        print(f"date range: {min(dated)} -> {max(dated)}")
    missing = len(rows) - len(dated)
    if missing:
        print(f"WARNING: {missing} videos still undated "
              f"(no captions fetched, so no info json)")


if __name__ == "__main__":
    sys.exit(main())
