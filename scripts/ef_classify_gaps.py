#!/usr/bin/env python
"""B-gate D3: classify the 7,110 provenance-gap transcripts.

Buckets (per B_GATE_DECISIONS.md):
  missing title only / missing channel only / missing both
  (each cross-cut by: recoverable from video_catalog?)
  missing canonical video identity (should be structurally impossible here —
  video_id is the transcript_cache key; verify)
  unable to reopen source (transcript text not actually readable)
Verdict per case: INDEX=YES with metadata_state=incomplete (Case A) when
identity+reopen hold; INDEX=NO provenance_unresolved (Case B) otherwise.
Receipt -> docs/evidence-fabric/benchmark/gap_classification.json
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ef import authority  # noqa: E402

TRANSCRIPTS_DB = authority.TRANSCRIPTS_DB
STATUS_DB = authority.STATUS_DB


def main() -> int:
    conn = sqlite3.connect(f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"attach database 'file:{STATUS_DB}?mode=ro' as status")

    rows = conn.execute("""
        select t.video_id, length(t.transcript) as tlen,
               a.title as a_title, a.channel_id as a_channel,
               v.title as v_title, v.channel_id as v_channel
        from transcript_cache t
        left join status.analysis_status a on a.video_id = t.video_id
        left join status.video_catalog v on v.video_id = t.video_id
        where a.channel_id is null or a.title is null
    """).fetchall()
    conn.close()

    rep = {"ran_at": datetime.now(timezone.utc).isoformat(),
           "total_gap_rows": len(rows), "buckets": {}, "detail": {}}

    def bucket(name):
        rep["buckets"][name] = rep["buckets"].get(name, 0) + 1

    missing_identity = []
    unreopenable = []
    case_a, case_b = [], []
    cat_recoverable = {"title": 0, "channel": 0, "both": 0}

    for r in rows:
        # canonical identity: video_id is the cache key; verify non-empty
        if not r["video_id"]:
            missing_identity.append(dict(r))
            continue
        # reopenability: transcript text present and non-degenerate
        if (r["tlen"] or 0) < authority.MIN_TRANSCRIPT_CHARS:
            unreopenable.append({"video_id": r["video_id"], "tlen": r["tlen"]})
            continue

        miss_title = not r["a_title"]
        miss_channel = not r["a_channel"]
        # deterministic recovery from video_catalog?
        rec_title = miss_title and bool(r["v_title"])
        rec_channel = miss_channel and bool(r["v_channel"])

        if miss_title and miss_channel:
            kind = "missing_both"
        elif miss_title:
            kind = "missing_title_only"
        else:
            kind = "missing_channel_only"
        bucket(kind)
        rep["detail"].setdefault(kind, []).append(r["video_id"])
        if rec_title or rec_channel:
            if rec_title and rec_channel:
                cat_recoverable["both"] += 1
            elif rec_title:
                cat_recoverable["title"] += 1
            else:
                cat_recoverable["channel"] += 1
        # identity + reopen hold -> Case A: indexable with incomplete metadata
        case_a.append(r["video_id"])

    bucket("missing_canonical_identity")
    bucket("unable_to_reopen")
    rep["buckets"]["missing_canonical_identity"] = len(missing_identity)
    rep["buckets"]["unable_to_reopen"] = len(unreopenable)
    rep["case_A_indexable_with_incomplete_metadata"] = len(case_a)
    rep["case_B_provenance_unresolved"] = len(missing_identity) + len(unreopenable)
    rep["video_catalog_recovery_available"] = cat_recoverable
    rep["missing_identity_sample"] = missing_identity[:5]
    rep["unreopenable_sample"] = unreopenable[:10]

    # verify sample reopen of Case A rows actually works end-to-end
    sample_ok, sample_fail = 0, []
    for vid in case_a[:25]:
        try:
            txt = authority.reopen_span(vid, 0, min(200, 10**9))
            if txt and len(txt) >= authority.MIN_TRANSCRIPT_CHARS:
                sample_ok += 1
            else:
                sample_fail.append(vid)
        except Exception as e:
            sample_fail.append(f"{vid}:{type(e).__name__}")
    rep["reopen_verification"] = {"sampled": min(25, len(case_a)),
                                  "ok": sample_ok, "failures": sample_fail}

    out = REPO / "docs" / "evidence-fabric" / "benchmark" / "gap_classification.json"
    out.write_text(json.dumps(
        {k: (v if not isinstance(v, list) or k.endswith("_sample")
             or k == "reopen_verification" else f"[{len(v)} ids]")
         for k, v in rep.items()}, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in
                      ["total_gap_rows", "buckets", "case_A_indexable_with_incomplete_metadata",
                       "case_B_provenance_unresolved", "video_catalog_recovery_available",
                       "reopen_verification"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
