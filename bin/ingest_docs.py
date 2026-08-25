#!/usr/bin/env python3
"""Stage 4b: ingest PDF/EPUB documents into a forge corpus.

The document-side twin of fetch_subtitles.py + clean_transcripts.py: takes
books/papers the user already possesses, extracts plain text, cleans it,
and writes the same corpus layout the synthesis stage expects:

    corpora/<name>/
      transcripts/<slug>.txt      one cleaned text per document
      meta/docs.jsonl             one record per document (title, author,
                                  sha256, words, extractor, source path)

Usage:
    ingest_docs.py --corpus corpora/reader-decoding FILE [FILE ...]

Extraction:
    .pdf   pdftotext (poppler); page headers/footers and bare page numbers
           are stripped by frequency analysis, hyphenation at line breaks
           is repaired.
    .epub  pandoc -t plain --wrap=none.
    .txt   copied through the same cleaner.

A document that extracts to fewer than MIN_WORDS words (default 5,000) is
recorded with "status": "too_short" and skipped — the usual cause is a
scanned PDF with no text layer; run OCR (e.g. ocrmypdf) and re-ingest.

Rights note: this stage handles books under copyright. The corpus dir must
stay in the PRIVATE references repo, and the synthesis stage's rule is
absolute here: synthesize, never transcribe — no verbatim passages in any
SKILL.md, and per-author attribution in every Provenance section.
"""
import argparse
import collections
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

MIN_WORDS = 5000


def slugify(name: str) -> str:
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-').lower()
    return s[:80] or 'doc'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def extract_pdf(path: Path) -> str:
    out = subprocess.run(['pdftotext', '-enc', 'UTF-8', str(path), '-'],
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f'pdftotext failed: {out.stderr.strip()[:200]}')
    return strip_page_furniture(out.stdout)


def extract_epub(path: Path) -> str:
    out = subprocess.run(['pandoc', '-t', 'plain', '--wrap=none', str(path)],
                         capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f'pandoc failed: {out.stderr.strip()[:200]}')
    return out.stdout


def strip_page_furniture(text: str) -> str:
    """Remove running headers/footers and page numbers from pdftotext output.

    Pages arrive separated by form feeds. A short line that recurs on many
    pages (>5% of pages, seen 5+ times) is furniture, not prose.
    """
    pages = text.split('\f')
    counts = collections.Counter()
    for page in pages:
        lines = [l.strip() for l in page.splitlines() if l.strip()]
        for l in set(lines[:3] + lines[-3:]):
            if len(l) < 80:
                counts[l] += 1
    threshold = max(5, len(pages) // 20)
    furniture = {l for l, n in counts.items() if n >= threshold}
    kept = []
    for page in pages:
        for line in page.splitlines():
            s = line.strip()
            if s in furniture:
                continue
            if re.fullmatch(r'[ivxlcdm]+|\d{1,4}', s, re.IGNORECASE):
                continue
            kept.append(line)
    return '\n'.join(kept)


def clean(text: str) -> str:
    text = text.replace('­', '')                      # soft hyphens
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)           # rejoin hyphenated breaks
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + '\n'


def parse_title_author(stem: str):
    """Filenames commonly end with '(Author Name)'; recover both parts."""
    m = re.search(r'\(([^()]+)\)[^()]*$', stem)
    if m:
        author = m.group(1).strip()
        title = stem[:m.start()].strip(' -.')
        return title or stem, author
    return stem, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--corpus', required=True,
                    help='corpus dir, e.g. corpora/reader-decoding')
    ap.add_argument('--min-words', type=int, default=MIN_WORDS)
    ap.add_argument('files', nargs='+')
    args = ap.parse_args()

    corpus = Path(args.corpus)
    tdir = corpus / 'transcripts'
    mdir = corpus / 'meta'
    tdir.mkdir(parents=True, exist_ok=True)
    mdir.mkdir(parents=True, exist_ok=True)

    records, failures = [], 0
    for f in args.files:
        path = Path(f)
        rec = {'source': str(path), 'file': path.name}
        try:
            title, author = parse_title_author(path.stem)
            rec.update(title=title, author=author, sha256=sha256(path))
            ext = path.suffix.lower()
            if ext == '.pdf':
                text, rec['extractor'] = extract_pdf(path), 'pdftotext'
            elif ext == '.epub':
                text, rec['extractor'] = extract_epub(path), 'pandoc'
            elif ext == '.txt':
                text, rec['extractor'] = path.read_text(errors='replace'), 'copy'
            else:
                raise RuntimeError(f'unsupported extension {ext}')
            text = clean(text)
            rec['words'] = len(text.split())
            if rec['words'] < args.min_words:
                rec['status'] = 'too_short'
                failures += 1
                print(f'SKIP  {path.name}: {rec["words"]} words '
                      f'(< {args.min_words}; scanned PDF? try ocrmypdf)')
            else:
                slug = slugify(title)
                (tdir / f'{slug}.txt').write_text(text)
                rec.update(status='ok', transcript=f'transcripts/{slug}.txt')
                print(f'OK    {path.name}: {rec["words"]:,} words -> {slug}.txt')
        except Exception as e:
            rec['status'] = f'error: {e}'
            failures += 1
            print(f'FAIL  {path.name}: {e}', file=sys.stderr)
        records.append(rec)

    with open(mdir / 'docs.jsonl', 'a') as out:
        for rec in records:
            out.write(json.dumps(rec, ensure_ascii=False) + '\n')
    ok = sum(1 for r in records if r.get('status') == 'ok')
    total_words = sum(r.get('words', 0) for r in records if r.get('status') == 'ok')
    print(f'\n{ok}/{len(records)} ingested, {total_words:,} words total, '
          f'{failures} skipped/failed. Meta: {mdir / "docs.jsonl"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
