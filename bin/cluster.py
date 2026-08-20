#!/usr/bin/env python3
"""Multi-label assignment of transcripts to skill domains by title regex.

The taxonomy is not hardcoded here -- the agent writes build/domains.json for
each corpus, since the domains that carve up a book-editing channel are useless
for a woodworking one. This script only applies it, so the coverage record is
reproducible and auditable independently of whoever generated the taxonomy.

Labels are not exclusive: a query-letter video is evidence for both the
submission-package skill and the agent-strategy skill.

Output build/clusters.json is the coverage record each SKILL.md cites in its
Provenance section.

domains.json: {"domain-name": "regex|alternation", ...}
"""
import argparse
import json
import os
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", help="corpora/<slug> directory")
    ap.add_argument("--domains", help="default: <corpus>/build/domains.json")
    a = ap.parse_args()

    corpus = os.path.abspath(a.corpus)
    trans = os.path.join(corpus, "transcripts")
    build = os.path.join(corpus, "build")
    os.makedirs(build, exist_ok=True)
    dom_path = a.domains or os.path.join(build, "domains.json")
    if not os.path.exists(dom_path):
        print(f"missing {dom_path} -- write the taxonomy first", file=sys.stderr)
        return 1
    with open(dom_path) as f:
        domains = json.load(f)

    titles = {}
    with open(os.path.join(corpus, "meta", "videos.jsonl")) as f:
        for line in f:
            d = json.loads(line)
            titles[d["id"]] = (d.get("title") or "", d.get("view_count") or 0)

    have = sorted(n[:-4] for n in os.listdir(trans) if n.endswith(".txt"))
    manifest = {k: [] for k in domains}
    labelled = set()
    for v in have:
        title, views = titles.get(v, ("", 0))
        wc = len(open(os.path.join(trans, v + ".txt"), encoding="utf-8").read().split())
        for name, pat in domains.items():
            if re.search(pat, title, re.I):
                manifest[name].append({"id": v, "title": title, "views": views, "words": wc})
                labelled.add(v)

    for k in manifest:
        # views first: on a single channel it is the closest available proxy
        # for which treatment of a topic the audience found most complete
        manifest[k].sort(key=lambda r: -r["views"])
        print(f"{k:24s} {len(manifest[k]):4d} videos  "
              f"{sum(r['words'] for r in manifest[k]):>8,d} words")

    unlabelled = [v for v in have if v not in labelled]
    print(f"\nlabelled {len(labelled)}/{len(have)}   unlabelled: {len(unlabelled)}")
    if unlabelled:
        print("sample unlabelled titles (widen the taxonomy if these matter):")
        for v in unlabelled[:15]:
            print(f"  {titles.get(v, ('?',))[0][:78]}")

    with open(os.path.join(build, "clusters.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    print(f"-> {build}/clusters.json")


if __name__ == "__main__":
    sys.exit(main())
