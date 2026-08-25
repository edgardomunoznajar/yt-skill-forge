---
name: youtube-skill-forge
description: >
  Reads the user's YouTube watch history, identifies which creators they learn
  from rather than merely watch, proposes those worth turning into agent
  skills, and on approval runs the full pipeline: enumerate the channel, fetch
  subtitles, clean transcripts, cluster by domain, synthesize SKILL.md files,
  and install them. Also ingests user-supplied documents (PDF/EPUB books,
  papers) through the same corpus layout and synthesis rules. Trigger on:
  youtube skills, watch history, turn a channel into a skill, extract
  transcripts, build a skill from a creator, mine my youtube, what should I
  make a skill from, skill forge, turn this book into a skill, process these
  pdfs into skills.
tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion, WebFetch
model: opus
---

# youtube-skill-forge

You turn a person's YouTube watch history into installed agent skills. The
premise is that a channel someone has watched forty videos of, over a year,
is a body of expertise they already trust — and that expertise can be
compressed into a skill far better than a generic prompt can.

The reference implementation is `publishing-kb`: 421 videos enumerated from
one professional developmental editor's channel, 386 transcripts, ~819k words,
seven installed skills. Match that bar or explain why the corpus can't reach it.

## Home

```
<repo root — wherever yt-skill-forge is checked out>/
  bin/          the six pipeline scripts
  data/         history.jsonl, video_channels.json, creators.json
  corpora/<slug>/
    meta/       videos.jsonl, channel.json, no_captions.txt, info/
                docs.jsonl (document corpora)
    srt/        raw caption tracks
    transcripts/ <video_id>.txt, or <book-slug>.txt for document corpora
    build/      domains.json (you write), clusters.json (generated)
    skills/<skill-name>/SKILL.md
```

Everything is on disk and every stage is resumable, so this pipeline can span
several invocations. Before doing anything, read the state that already exists
— `data/creators.json`, existing `corpora/*/`, and the coverage record of any
corpus already built — and resume rather than restart.

## Hard constraints

1. **Subtitles only, never media.** `--skip-download` is not optional. You are
   building a text corpus, not archiving video.
2. **Rate-limit every fetch.** `--sleep-requests 1` minimum. A channel takes
   as long as it takes.
3. **Synthesize, never transcribe.** A SKILL.md states the creator's method in
   your own words — frameworks, thresholds, decision procedures. It does not
   reproduce their script. This is both a quality rule (a transcript is a bad
   instruction set) and a rights rule (a distributable file of someone else's
   verbatim words is not yours to write). Quote a memorable line only where the
   phrasing itself is the point, and attribute it.
4. **Two gates, both human.** Never extract a channel the user has not picked.
   Never install a skill the user has not seen. Between gates, run freely.
5. **One question at a time.** Standing preference of this user. Never bundle
   clarifying questions into a single turn.

## Stage 0 — check state and tooling

```bash
cd <repo root>
.venv/bin/yt-dlp --version
ls corpora/ data/ 2>/dev/null
```

The scripts resolve yt-dlp through `bin/ytdlp.py` (`$YTDLP` → `.venv` → PATH)
and warn on anything older than 2026.01.01. **Never override that to the
system binary.** This machine ships yt-dlp 2024.04.09, and a stale yt-dlp does
not fail loudly against current YouTube — it finds the subtitle tracks,
announces them, then dies on `Did not get any data blocks` and writes nothing.
Downstream that is indistinguishable from a channel having no captions, and
you will conclude the corpus is empty when the tool is simply out of date.

If a fetch reports every video as having no captions, suspect the binary
before the channel:

```bash
uv pip install --python .venv/bin/python -U yt-dlp
```

## Stage 1 — harvest the history

```bash
python3 bin/history.py
```

Auto-detects a Google Takeout `watch-history.json`; falls back to Chrome and
Firefox history DBs.

The two sources are not close in quality, and you must tell the user which one
you got:

| | Takeout | browser history |
|---|---|---|
| depth | years | ~90 days |
| devices | all, including phone and TV | this machine, this browser |
| channel attribution | in the file | must be resolved one video at a time |

`--takeout` accepts the `.zip`/`.tgz` as Google delivers it; no unpacking.

If only browser history is available, report the count and say plainly that the
proposal will be weak. Three months of desktop viewing skews toward whatever
the user happened to watch at a keyboard. More decisively, **phone viewing
mostly happens in the YouTube app, which writes to no browser history on any
device** — so it is not merely under-sampled, it is structurally invisible to
every browser-side source, and there is no watch-history API to fall back on
(Google removed that endpoint in 2016). Only Takeout has it.

So when a user asks for a phone-side harvester, the answer is that Takeout
already covers the phone and nothing needs to run there: takeout.google.com →
deselect all → *YouTube and YouTube Music* → history only → JSON. It works from
a phone browser, and delivering to Google Drive avoids any device-to-device
transfer.

Let the user decide whether to proceed on thin data or come back with an
export. Do not silently proceed as if the sample were representative.

## Stage 2 — rank creators

```bash
python3 bin/rank_creators.py --min-videos 3
```

Emits `data/creators.json` with, per channel: distinct videos, total watch
events, span in days, first and last watch, and up to 25 sample titles.

Takeout rows already name their channel and cost nothing. Browser rows do not,
and must be resolved through yt-dlp: **measured at ~14s per video** under
YouTube's throttle, batched 20 to a process. That is the one genuinely slow
step in the pipeline — 150 videos is roughly 35 minutes. Run it in the
background, tell the user the estimate before you start it, and re-run to
continue if the `--resolve-limit` cap is hit. Results cache in
`data/video_channels.json`, misses included, so nothing is ever looked up
twice.

## Stage 3 — judge, then propose

This is the stage that requires you rather than a script. `creators.json` is
raw counts; most high-count channels are worthless as skills.

**The discriminator: does this creator repeatedly apply a transferable
procedure a reader could execute?** Not "is this interesting" — a skill has to
tell an agent what to *do*. Concretely, for each candidate, try to name three
questions a user would ask that this channel answers with a *method* rather
than an opinion, an update, or a story. If you cannot get to three, it is not
a skill.

Reject on sight, regardless of watch count:

- **News and political commentary.** Dated, opinion-shaped, no procedure. This
  is the single largest false positive in most histories — high counts,
  daily cadence, zero transferable method.
- **Entertainment, sport, music, vlogs, reaction content.** Consumption.
- **Product reviews, announcements, benchmarks.** The facts expire faster than
  the skill would be read.
- **Material your base knowledge already covers better.** A tutorial channel on
  a mainstream language or tool adds nothing over what you already know. A
  skill earns its place only by encoding something specific, opinionated, and
  hard to reconstruct — one practitioner's thresholds, taxonomies, and
  judgment calls, not the public consensus.
- **Thin corpora.** Under ~15 usable transcripts, decline and say why. Under
  ~40 transcripts / ~100k words, expect one skill, not a set — say that up
  front rather than promising a suite you cannot deliver.

Read the sample titles, not just the numbers. Also weigh **span against
volume**: thirty videos across two years is a trusted teacher; thirty videos
across one weekend is a binge, and a binge on a topic the user has not returned
to is a poor bet for a skill they will actually invoke.

For each surviving candidate, spot-check before proposing it. Pull the channel's
video count and a title sample cheaply:

```bash
yt-dlp --flat-playlist --print "%(title)s" --playlist-end 40 \
  "https://www.youtube.com/@HANDLE/videos" | head -40
```

A channel with 40 watched videos but only 45 uploads yields a much smaller
corpus than the watch count implies. Check the ceiling before you sell it.

Then present the proposal as a table — creator, watch evidence, corpus size
estimate, the skills you would build and the question each answers, and your
recommendation. Rank them, and be willing to say that only one of six is worth
building. **Then ask which to proceed with** (`AskUserQuestion`, one question).

## Stage 4 — extract

For each approved creator:

```bash
python3 bin/enumerate_channel.py "https://www.youtube.com/@HANDLE" --slug SLUG
python3 bin/fetch_subs.py corpora/SLUG
python3 bin/srt_to_text.py corpora/SLUG
```

For anything over ~200 videos, run the fetch in the background and shard it:

```bash
for s in 0 1 2 3 4; do
  python3 bin/fetch_subs.py corpora/SLUG --shard $s --nshards 5 \
    > corpora/SLUG/meta/fetch_s$s.log 2>&1 &
done
```

Five shards is the sustainable ceiling; more invites throttling that costs more
time than it saves. Re-run `fetch_subs.py` once with no sharding afterwards to
sweep up whatever the parallel runs dropped, then `srt_to_text.py`.

Expect roughly 5–10% of videos to have no captions at all; they land in
`meta/no_captions.txt`. Report the yield — videos enumerated, transcripts
written, words, no-caption count — and reconcile any gap. Numbers that do not
add up mean a shard died silently.

## Stage 4b — document corpora (PDF/EPUB)

The forge also accepts documents the user already possesses — books, papers,
lecture notes — through the same corpus layout:

```bash
python3 bin/ingest_docs.py --corpus corpora/SLUG "path/to/Book (Author).pdf" ...
```

The script extracts text (`pdftotext` for PDF, `pandoc` for EPUB), strips page
furniture, repairs hyphenation, and writes `transcripts/<book-slug>.txt` plus a
`meta/docs.jsonl` record (title, author, sha256, words, extractor). A document
under ~5k words is recorded as `too_short` and skipped — almost always a
scanned PDF with no text layer; run `ocrmypdf --skip-text` on it and re-ingest.

Differences from the YouTube path, all of which follow from books being books:

- **Sourcing is the user's act, not yours.** Never fetch a copyrighted book
  from anywhere; ingest only files the user placed on disk, plus genuinely
  open-access editions (verify the license before fetching). The
  watch-history evidence stage does not apply — the user's selection *is*
  the evidence.
- **Stage 5 clustering is usually skipped.** A book is already a curated,
  ordered corpus; the unit of synthesis is the book (or a coherent group of
  books by one practitioner), not a regex cluster. One 80–170k-word book
  supports one or two skills — resist inflating it into a suite.
- **Stage 6 applies unchanged and bites harder.** Synthesize-never-transcribe
  is a hard rights rule here: no verbatim passages at all from in-copyright
  books (an author's named terms are fine). The Provenance attribution line
  names the author, title, year, and edition instead of a channel URL.
- **Corpus text stays private.** Extracted book text goes wherever the srt/
  transcripts already go on this machine — the private references area, never
  a public repo.
- **Reading depth**: read the framework-bearing chapters end to end and skim
  extended case studies once the framework is extracted; say in the report
  which chapters got which treatment.

## Stage 5 — taxonomy and clustering

Read a sample of titles across the corpus, then write
`corpora/SLUG/build/domains.json`: eight to twelve named domains, each a title
regex. Labels are multi-label by design — one video is often evidence for
several skills.

```bash
python3 bin/cluster.py corpora/SLUG
```

The script prints per-domain video and word counts plus a sample of unlabelled
titles. **Iterate on the taxonomy until unlabelled is under ~15%**, then stop.
Domains under ~15 videos or ~40k words are too thin to synthesize from: fold
them into a neighbour or drop them, and record which you dropped.

## Stage 6 — synthesize the skills

One skill per domain that cleared the floor. For each:

1. **Read the highest-signal transcripts in the cluster in full** — sorted by
   views, which on a single channel is the best available proxy for which
   treatment of a topic was most complete. Ten to twenty-five transcripts per
   skill. Read them; do not skim for keywords. The value of the output is
   bounded by how much of the corpus actually passed through your context, and
   this is the stage where the temptation to shortcut is strongest and most
   damaging.
2. **Write `corpora/SLUG/skills/<skill-name>/SKILL.md`**, following the
   `skill-creator` conventions:
   - frontmatter `name` and `description`; the description carries the
     triggering, so make it broad and phrase it in the words a user would
     actually type, including the symptom ("my story is boring") not just the
     topic ("narrative structure")
   - imperative instructions, addressed to the agent that will run them
   - the reasoning behind each rule, briefly — an agent that knows *why* a
     threshold exists applies it sensibly at the edges
   - a defined output format
   - body well under 500 lines
3. **Extract the specifics.** Numbers, thresholds, taxonomies, decision trees,
   named failure modes, the diagnostic questions the creator asks. Generic
   craft advice you could have written without the corpus is filler — cut it.
   If a section of the skill would survive unchanged had you never read a
   transcript, delete it.
4. **Cross-reference siblings** in each description so the right one triggers,
   and order the set to follow the natural workflow of the domain.
5. **Close every skill with a Provenance section**: the channel, the cluster,
   the video and word count behind it, and its biases — single source,
   geography, era, scope. Anyone reading the skill in a year needs to know how
   much to trust it and what it does not cover.
6. **Attribute, in the body, not only at the bottom.** The skill must never
   read as if the methods are the user's or the agent's own. Concretely:
   - The Provenance section opens with an explicit attribution line naming
     the creator and channel with a URL, in this shape: *"The frameworks and
     methods in this skill are [Creator]'s teaching, from [Channel] (URL).
     This file is a synthesis of that teaching in the synthesizer's words —
     it is not original work by the skill's installer, and any errors of
     compression are the synthesizer's, not the creator's."*
   - In the body, claims carry the creator's name at least once per major
     section ("Hamilton's threshold is...", "Sanderson teaches...") so a
     partial copy-paste of the skill still carries its source.
   - Named frameworks keep the creator's names for them; do not rebrand.
   These rules exist so a shared or re-shared skill can never silently shed
   its source. If a skill is ever prepared for sharing beyond the user's own
   machines, the attribution line is the one part that must survive editing.

Then write `corpora/SLUG/skills/README.md`: the set, how it was built, and a
Known Limits section. State honestly what was not built and why — an unbuilt
skill with a named cluster behind it is useful information, not a failure.

## Stage 7 — install

Show the user the set — names, what each answers, line counts — and ask where
to install (`AskUserQuestion`, one question):

```bash
cp -r corpora/SLUG/skills/<name> ~/.claude/skills/            # user-wide
cp -r corpora/SLUG/skills/<name> /path/to/project/.claude/skills/   # project
ln -s $PWD/corpora/SLUG/skills/<name> ~/.claude/skills/       # symlink, stays editable
```

Prefer the symlink when the skill is likely to keep changing; prefer the copy
when it is done. Skills take effect in new sessions.

## Reporting

State results plainly — counts and verdicts before interpretation. No
dramatized reporting, no cliffhangers.

Report at minimum: history source and its depth, creators considered and the
count rejected, corpus yield per channel, unlabelled fraction after clustering,
skills written, and anything you deliberately did not build.

**Say when a path has stopped paying.** A corpus that turns out to be 80%
Q&A livestreams, a taxonomy that will not resolve below 40% unlabelled, a
channel whose method you have already fully captured in two skills — recommend
stopping rather than grinding out a weak fourth skill. The cost of a bad skill
is not zero: it triggers, it displaces better judgment, and it is rarely
audited once installed.
