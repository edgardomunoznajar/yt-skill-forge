#!/usr/bin/env python3
"""Convert a corpus's fetched .srt captions to clean plain text.

Handles the YouTube auto-caption rolling-window artifact, where each cue
repeats the tail of the previous cue -- left alone it roughly doubles the
word count and wrecks any downstream reading. Prefers a manual English track
over the auto-generated one when both exist.

Generalized from publishing-kb/bin/srt_to_text.py.
"""
import argparse
import html
import os
import re
import sys

TS = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->")
IDX = re.compile(r"^\d+$")
TAG = re.compile(r"<[^>]+>")


def parse_cues(path):
    cues, cur = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if TS.match(line):
                continue
            if not line.strip():
                if cur:
                    cues.append(cur)
                    cur = []
                continue
            if IDX.match(line.strip()) and not cur:
                continue
            text = re.sub(r"\s+", " ", html.unescape(TAG.sub("", line)).strip())
            if text:
                cur.append(text)
    if cur:
        cues.append(cur)
    return cues


def dedupe(cues):
    """Emit lines, dropping the rolling-window repetition between cues."""
    out = []
    for cue in cues:
        for line in cue:
            # the window is two cues deep in practice; three is a safe margin
            if line in out[-3:]:
                continue
            out.append(line)
    return out


def reflow(lines):
    """Join caption lines into sentence-ish paragraphs."""
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    paras, buf = [], []
    for s in sentences:
        buf.append(s)
        if len(buf) >= 5:
            paras.append(" ".join(buf))
            buf = []
    if buf:
        paras.append(" ".join(buf))
    return "\n\n".join(paras)


def pick_best(vid, files):
    """Manual en track beats auto (en-orig / auto-generated)."""
    order = {f"{vid}.en.srt": 0, f"{vid}.en-orig.srt": 1}
    return sorted(files, key=lambda n: order.get(n, 2))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", help="corpora/<slug> directory")
    ap.add_argument("--min-chars", type=int, default=200,
                    help="discard transcripts shorter than this")
    a = ap.parse_args()

    corpus = os.path.abspath(a.corpus)
    srtdir = os.path.join(corpus, "srt")
    trans = os.path.join(corpus, "transcripts")
    os.makedirs(trans, exist_ok=True)

    groups = {}
    for n in os.listdir(srtdir):
        if n.endswith(".srt"):
            groups.setdefault(n.split(".")[0], []).append(n)

    written = skipped = empty = words = 0
    for vid, files in sorted(groups.items()):
        out_path = os.path.join(trans, vid + ".txt")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            skipped += 1
            continue
        body = reflow(dedupe(parse_cues(os.path.join(srtdir, pick_best(vid, files)))))
        if len(body) < a.min_chars:
            empty += 1
            continue
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        written += 1
        words += len(body.split())

    total = sum(len(open(os.path.join(trans, n), encoding="utf-8").read().split())
                for n in os.listdir(trans) if n.endswith(".txt"))
    print(f"written={written} already={skipped} too_short={empty} groups={len(groups)}")
    print(f"corpus now {len(os.listdir(trans))} transcripts, {total:,} words")


if __name__ == "__main__":
    sys.exit(main())
