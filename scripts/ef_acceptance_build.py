#!/usr/bin/env python
"""Build the C1 final acceptance set from the untouched region (videos
[157:200] per category). Auto: identifier strata with recorded prod-scale
df (df1 / df2-100 / df101-1000 / punct) + common terms + twins.
--mode excerpts prints 70 compact excerpts for hand authoring
(semantic_natural / semantic_technical / comparison_questions)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import authority, chunking, routing  # noqa: E402

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
START = 200
SPAN = 43
IDENT_SCAN = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+"
    r"|[a-z]+(?:_[a-z0-9]+)+"
    r"|[A-Za-z]+[a-z][A-Z][A-Za-z0-9]*"
    r"|[A-Za-z]+-[0-9][A-Za-z0-9-]*"
    r"|[A-Z]{2,}[A-Za-z0-9]*"
    r"|[A-Za-z]+[0-9][A-Za-z0-9]*)\b")


def region_rows():
    conn = sqlite3.connect(f"file:{authority.TRANSCRIPTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"attach database 'file:{authority.STATUS_DB}?mode=ro' as status")
    rows = conn.execute("""
        select t.video_id, t.transcript, a.title,
               coalesce(nullif(cm.category,''),'Uncategorized') as category
        from transcript_cache t
        join status.analysis_status a on a.video_id = t.video_id
        left join status.channel_metadata cm on cm.channel_id = a.channel_id
        where length(t.transcript) >= 100
          and a.channel_id is not null and a.title is not null
          and t.terminal_id not like 'test%'
        order by t.video_id asc
    """).fetchall()
    conn.close()
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(dict(r))
    out = []
    for cat, lst in by_cat.items():
        out.extend(lst[START:START + SPAN])
    return out


def df_of(tok):
    return routing.document_frequency(tok)


def build_auto():
    rows = region_rows()
    fts = sqlite3.connect(f"file:{routing.FTS_DB}?mode=ro", uri=True)
    out = {"exact_df1": [], "exact_df2_100": [], "exact_df101_1000": [],
           "punct_heavy": [], "near_twins": [],
           "common_lexical": [{"query": t} for t in
                              ["YouTube", "Google", "Python", "ChatGPT",
                               "iPhone", "NVIDIA", "JavaScript", "Windows"]]}
    seen = set()

    def take(bucket, tok, row, chunk_id, d):
        if tok.lower() in seen:
            return False
        seen.add(tok.lower())
        out[bucket].append({"query": tok, "positive_video": row["video_id"],
                            "positive_chunk": chunk_id, "df": d,
                            "category": row["category"]})
        return True

    caps = {"exact_df1": 35, "exact_df2_100": 35, "exact_df101_1000": 25,
            "punct_heavy": 18}
    punct_re = re.compile(r"\b[a-zA-Z0-9]+(?:[./:_-][a-zA-Z0-9]+){2,}\b")
    for row in rows:
        chs = chunking.chunk_transcript(f"{row['video_id']}:transcript",
                                        row["transcript"])
        for ch in chs[1:] or chs:
            toks = []
            for m in IDENT_SCAN.finditer(ch.text):
                toks.append(m.group(0))
            for m in punct_re.finditer(ch.text):
                toks.append(m.group(0))
            placed = False
            for tok in toks:
                if not (4 <= len(tok) <= 40):
                    continue
                d = df_of(tok)
                bucket = ("exact_df1" if d == 1 else
                          "exact_df2_100" if d <= 100 else
                          "exact_df101_1000" if d <= 1000 else None)
                if bucket and len(out[bucket]) < caps[bucket] and \
                        routing.identifier_shaped(tok):
                    placed = take(bucket, tok, row, ch.chunk_id, d)
                elif len(out["punct_heavy"]) < caps["punct_heavy"] and \
                        punct_re.fullmatch(tok):
                    placed = take("punct_heavy", tok, row, ch.chunk_id, d) or placed
                if placed:
                    break
            if all(len(out[k]) >= caps[k] for k in caps):
                break
        if all(len(out[k]) >= caps[k] for k in caps):
            break
    # twins from df1
    for it in out["exact_df1"][:12]:
        tok = it["query"]
        mutant = tok[:-1] + ("3" if tok[-1] != "3" else "7")
        out["near_twins"].append({"twin": tok, "mutant": mutant,
                                  "positive_chunk": it["positive_chunk"],
                                  "mutant_df": df_of(mutant)})
    fts.close()
    payload = json.dumps(out, indent=1)
    (BENCH / "acceptance_c2_auto.json").write_text(payload, encoding="utf-8")
    print({k: len(v) for k, v in out.items()})


def mode_excerpts():
    rows = region_rows()
    printed = 0
    for row in rows:
        if printed >= 70:
            break
        chs = chunking.chunk_transcript(f"{row['video_id']}:transcript",
                                        row["transcript"])
        if not chs:
            continue
        mid = chs[min(1, len(chs) - 1)]
        if len(mid.text) < 300:
            continue
        print("=" * 60)
        print(f"video: {row['video_id']}  cat: {row['category']}")
        print(f"title: {row['title'][:90]}")
        print(f"excerpt: {mid.text[:350]}")
        printed += 1
    print(f"# {printed} excerpts")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "excerpts", "seal"],
                    required=True)
    a = ap.parse_args()
    if a.mode == "auto":
        build_auto()
    elif a.mode == "excerpts":
        mode_excerpts()
    else:
        h = ""
        for f in ("acceptance_c2_auto.json", "acceptance_c2_hand.json"):
            p = BENCH / f
            if p.exists():
                h += hashlib.sha256(p.read_bytes()).hexdigest()[:16] + " "
        (BENCH / "acceptance_c2_seal.txt").write_text(
            f"sealed before C1 final replay\nfiles: auto hand\nsha256[:16]: {h}\n",
            encoding="utf-8")
        print("sealed:", h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
