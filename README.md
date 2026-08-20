# yt-skill-forge

Turn YouTube watch history into installed agent skills.

Generalized from `publishing-kb`, which did this once by hand for a single
channel: 421 videos enumerated, 386 transcripts, ~819k words, seven skills.
That corpus is the reference implementation — every script here was validated
by reproducing its output exactly.

The agent that drives all of this ships in `agents/youtube-skill-forge.md` —
copy it to `~/.claude/agents/` to install it, then ask for it by name or say
something like "mine my youtube history for skills."

Two things this repo deliberately does not contain, and yours shouldn't either
if you fork it: your watch history (`data/`) is personal, and the fetched
transcripts (`corpora/`) are the creators' words, not yours to republish. Both
are gitignored; keep them local or in a private repo. The skills the pipeline
synthesizes must attribute the creator — the agent file enforces this — because
the frameworks in them are the creator's teaching, not your original work.

## Pipeline

```bash
python3 bin/history.py                                   # history  -> data/history.jsonl
python3 bin/rank_creators.py                             #          -> data/creators.json
                                                         # [agent proposes, user picks]
python3 bin/enumerate_channel.py "https://www.youtube.com/@HANDLE" --slug SLUG
python3 bin/fetch_subs.py    corpora/SLUG                # subtitles -> corpora/SLUG/srt/
python3 bin/srt_to_text.py   corpora/SLUG                #           -> transcripts/
                                                         # [agent writes build/domains.json]
python3 bin/cluster.py       corpora/SLUG                #           -> build/clusters.json
                                                         # [agent synthesizes skills/]
```

Every stage is resumable and idempotent. Interrupting a fetch costs one batch.

## Layout

```
bin/            pipeline scripts, stdlib only apart from yt-dlp
data/           history.jsonl, video_channels.json (cache), creators.json
corpora/<slug>/
  meta/         videos.jsonl, channel.json, no_captions.txt, info/
  srt/          raw caption tracks, several per video
  transcripts/  <video_id>.txt, cleaned prose
  build/        domains.json (authored), clusters.json (generated)
  skills/       <skill-name>/SKILL.md
```

## History sources

`history.py` prefers a Google Takeout export and falls back to browser history.
They are not interchangeable:

| | Takeout | Chrome / Firefox |
|---|---|---|
| depth | years | ~90 days |
| devices | all, including phone and TV | this machine, this browser |
| channel names | in the file, free | one yt-dlp lookup per video, ~14s each |

Get the good one at [takeout.google.com](https://takeout.google.com/): deselect
all → *YouTube and YouTube Music* → history only → JSON. It works from a phone
browser, and can be delivered to Google Drive instead of downloaded.

`--takeout` accepts the `.zip` or `.tgz` exactly as Google delivers it — no
unzipping, no renaming:

```bash
python3 bin/history.py --takeout ~/Downloads/takeout-20260810T120000Z-001.zip
```

Bare `.zip`/`.tgz` files matching `~/Downloads/takeout-*` are auto-detected too.

Browser-only mode works and is honest about being a thin sample, but note *why*
it is thin: phone viewing mostly happens in the YouTube **app**, which touches
no browser history on any device. No browser-side harvester, desktop or mobile,
can see it. Only the server-side Takeout record has it.

## yt-dlp

Pinned in `.venv`. **Do not rely on the system binary** — this machine ships
2024.04.09, and against current YouTube a stale yt-dlp fails in the worst
possible way: it locates the subtitle tracks, announces them, then dies on
`Did not get any data blocks` and writes nothing. Downstream that is
indistinguishable from a channel having no captions. `bin/ytdlp.py` resolves
`$YTDLP` → `.venv` → `PATH` and warns below 2026.01.01.

Two flags are load-bearing and were both discovered the hard way:

- `--ignore-no-formats-error` — without it, yt-dlp runs format selection even
  under `--skip-download` and aborts on age-restricted, member-only, and
  region-locked videos *after* finding their subtitles.
- `--sleep-requests 1` — subtitles only, never media, always rate-limited.

Refresh with:

```bash
uv pip install --python .venv/bin/python -U yt-dlp
```

## Measured rates

| step | rate |
|---|---|
| channel enumeration | 421 videos in ~20s (flat playlist) |
| channel resolution (browser history only) | ~14s per video |
| subtitle fetch | ~10s per video, 5 shards sustainable |
| caption yield | expect 5–10% of videos to have none |
