#!/usr/bin/env python
"""C-gate item 6: build ≥50 UNTOUCHED identifier acceptance cases.

Fresh region: per-category videos AFTER the B.1 holdout window
(FROZEN_CAP=120..131 used by B.1; acceptance draws 132..156). Never scored
against any lane/model; sealed until the C acceptance replay.
Output: benchmark/identifier_acceptance_queries.json (with seal hash).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ef import authority  # noqa: E402

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
START = 132          # after B.1 holdout window (120..131)
SPAN = 25            # videos per category to consider
N_NEED = 55

IDENT = re.compile(
    r"\b(?:[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}|[a-z]+(?:\.[a-z0-9]+){1,}"
    r"|[A-Za-z]*[a-z][A-Z][A-Za-z]*|[A-Z]{3,}[0-9]*"
    r"|[a-zA-Z]+[0-9]+[a-zA-Z0-9.-]*)\b")


def main() -> int:
    conn = sqlite3.connect(f"file:{authority.TRANSCRIPTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"attach database 'file:{authority.STATUS_DB}?mode=ro' as status")
    rows = conn.execute("""
        select t.video_id, t.transcript,
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

    from ef import chunking
    queries, used = [], set()
    for cat in sorted({r["category"] for r in rows}):
        cat_rows = [r for r in rows if r["category"] == cat]
        window = cat_rows[START:START + SPAN]
        for r in window:
            if r["video_id"] in used:
                continue
            chs = chunking.chunk_transcript(
                f"{r['video_id']}:transcript", r["transcript"])
            # try mid chunks (more identifier-dense than intros)
            for ch in chs[1:] or chs:
                toks = sorted(set(m.group(0) for m in IDENT.finditer(ch.text)))
                toks = [t for t in toks if 4 <= len(t) <= 40]
                if toks:
                    tok = toks[hash(r["video_id"]) % len(toks)]
                    queries.append({
                        "tier": "identifier_acceptance", "query": tok,
                        "positive_video": r["video_id"],
                        "positive_chunk": ch.chunk_id, "category": cat})
                    used.add(r["video_id"])
                    break
            if len(queries) >= N_NEED:
                break
        if len(queries) >= N_NEED:
            break

    payload = json.dumps(queries, indent=1)
    seal = hashlib.sha256(payload.encode()).hexdigest()[:16]
    out = BENCH / "identifier_acceptance_queries.json"
    out.write_text(payload, encoding="utf-8")
    (BENCH / "identifier_acceptance_seal.txt").write_text(
        f"sealed 2026-08-17 before C acceptance replay\nsha256[:16]={seal}\n"
        f"n={len(queries)} distinct_videos={len(used)}\n", encoding="utf-8")
    print(f"[accept] {len(queries)} queries / {len(used)} videos, seal={seal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
