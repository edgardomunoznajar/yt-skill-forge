#!/usr/bin/env python3
"""Resumable subtitle fetcher for a corpus.

Subtitles only, never media. Skips ids already converted to .txt, already
holding a non-empty .srt, or recorded as having no captions. Safe to re-run,
safe to interrupt -- the skip set is rebuilt from disk each time, so a killed
run costs at most one batch.

Generalized from publishing-kb/bin/fetch_subs.py, which pinned a single corpus.
"""
import argparse
import json
import os
import subprocess
import sys

import ytdlp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP = ytdlp.find()


def done_ids(trans, srtdir, nocap):
    done = set()
    if os.path.isdir(trans):
        done |= {n[:-4] for n in os.listdir(trans) if n.endswith(".txt")}
    if os.path.isdir(srtdir):
        for n in os.listdir(srtdir):
            # a zero-byte .srt means the fetch produced nothing usable; do not
            # let it mask the id as done, or it can never be retried
            if n.endswith(".srt") and os.path.getsize(os.path.join(srtdir, n)) > 0:
                done.add(n.split(".")[0])
    if os.path.exists(nocap):
        with open(nocap) as f:
            done |= {l.split()[0] for l in f if l.strip()}
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", help="corpora/<slug> directory")
    ap.add_argument("--batch", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0, help="stop after N ids (0 = all)")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between requests")
    a = ap.parse_args()

    corpus = os.path.abspath(a.corpus)
    meta = os.path.join(corpus, "meta")
    trans = os.path.join(corpus, "transcripts")
    srtdir = os.path.join(corpus, "srt")
    infodir = os.path.join(meta, "info")
    nocap = os.path.join(meta, "no_captions.txt")
    for d in (trans, srtdir, infodir):
        os.makedirs(d, exist_ok=True)

    all_ids = []
    with open(os.path.join(meta, "videos.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("{"):
                continue  # tolerate blank lines and hand-edited comments
            all_ids.append(json.loads(line)["id"])
    skip = done_ids(trans, srtdir, nocap)
    todo = [i for i in all_ids if i not in skip]
    if a.nshards > 1:
        todo = todo[a.shard::a.nshards]
    if a.limit:
        todo = todo[:a.limit]
    print(f"total={len(all_ids)} done={len(skip)} todo={len(todo)}", flush=True)

    for start in range(0, len(todo), a.batch):
        batch = todo[start:start + a.batch]
        cmd = [
            YTDLP,
            "--write-auto-subs", "--write-subs",
            "--sub-langs", "en.*",
            "--skip-download",
            "--convert-subs", "srt",
            "--write-info-json",
            "--no-warnings",
            "--ignore-errors",
            # without this yt-dlp still runs format selection even under
            # --skip-download, and aborts the whole video on "Requested format
            # is not available" *after* having already located the subtitle
            # tracks -- which silently yields a corpus of zero transcripts
            "--ignore-no-formats-error",
            "--sleep-requests", str(a.sleep),
            "-o", "%(id)s.%(ext)s",
            "-P", f"subtitle:{srtdir}",
            "-P", f"infojson:{infodir}",
            "-P", srtdir,
        ] + [f"https://www.youtube.com/watch?v={i}" for i in batch]
        print(f"[batch {start // a.batch + 1}/{-(-len(todo) // a.batch)}] "
              f"{len(batch)} ids", flush=True)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        have = {n.split(".")[0] for n in os.listdir(srtdir) if n.endswith(".srt")}
        missing = [i for i in batch if i not in have]
        if missing:
            with open(nocap, "a") as f:
                f.write("".join(i + "\n" for i in missing))
            print(f"  no captions: {len(missing)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
