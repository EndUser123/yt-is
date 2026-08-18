#!/usr/bin/env python
"""Evidence-Fabric-backed /wiki maintenance modes (consumer layer).

Authority boundary (L-gate): Evidence Fabric supplies retrieval
candidates + provenance. The CALLER (/wiki maintenance flow) owns
support/contradiction/staleness judgments. This tool returns
CANDIDATES and review signals — never truth verdicts from rank alone.

Modes:
  evidence      claim -> supporting-candidate set (unvalidated)
  contradiction claim -> contrast-framed candidates + source-diversity
                filter vs the claim's existing sources
  staleness     claim + last_verified -> newer-evidence review signal

All EF access via the production seam (ef-query contract, same process).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

def _captured_at(eu_id: str):
    """Reopen the authoritative captured_at for an EU via catalog PK."""
    import sqlite3
    from ef.catalog import CATALOG_DB
    try:
        conn = sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "select captured_at from eu where eu_id=?", (eu_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


CONTRAST_FRAMES = [
    "{claim} problems limitations criticism",
    "evidence against {claim}",
    "why {claim} fails or is wrong",
]


def _iso(dt) -> str:
    return dt.isoformat() if dt else ""


def _parse_ts(s: str):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def mode_evidence(pq, claim: str, top_k: int = 8) -> dict:
    res = pq.relevant(claim, limit=top_k)
    candidates = []
    for i, r in enumerate(res):
        candidates.append({
            "role": "retrieved_candidate",       # NOT "support"
            "rank": i + 1, "score": round(r.score, 4),
            "evidence_text": r.snippet[:400],
            "source_url": r.url, "video_id": r.video_id,
            "channel": r.channel_title or r.channel_id,
            "char_span": [r.start_char, r.end_char],
            "eu_id": r.eu_id, "retrieval_paths": list(r.retrieval_paths),
        })
    return {"mode": "wiki_evidence", "claim": claim,
            "note": "candidates are UNVALIDATED; /wiki owns the support "
                    "judgment — rank is not truth",
            "candidates": candidates}


def mode_contradiction(pq, claim: str, existing_sources: list[str],
                       top_k: int = 8) -> dict:
    """Contrast-framed retrieval + source-diversity filter: hits whose
    channel matches an existing claim source are demoted (still listed,
    flagged same_source) so contradiction search isn't an echo."""
    seen = {}
    for frame in CONTRAST_FRAMES[:2]:
        for r in pq.relevant(frame.format(claim=claim), limit=top_k):
            if r.chunk_id not in seen:
                seen[r.chunk_id] = r
    ranked = sorted(seen.values(), key=lambda r: -r.score)[:top_k * 2]
    ex = set(existing_sources or [])
    candidates, diverse = [], []
    for i, r in enumerate(ranked):
        same = (r.channel_id in ex) or (r.channel_title in ex)
        cand = {
            "role": "contradiction_candidate",  # NOT "contradicts"
            "rank": i + 1, "score": round(r.score, 4),
            "evidence_text": r.snippet[:400], "source_url": r.url,
            "video_id": r.video_id, "channel": r.channel_title or r.channel_id,
            "char_span": [r.start_char, r.end_char],
            "same_source_as_claim": same,
        }
        candidates.append(cand)
        if not same:
            diverse.append(cand)
    return {"mode": "wiki_contradiction", "claim": claim,
            "note": "candidates with contrast framing; /wiki owns the "
                    "contradiction judgment; same_source hits are echoes",
            "diverse_candidates": diverse[:top_k],
            "all_candidates": candidates[:top_k * 2]}


def mode_staleness(pq, claim: str, last_verified: str,
                   top_k: int = 8) -> dict:
    """Compare candidate captured/published timestamps vs wiki
    last_verified. Newer evidence => eligible for review, NOT auto-stale."""
    lv = _parse_ts(last_verified)
    res = pq.relevant(claim, limit=top_k)
    newer = []
    for r in res:
        ts = _parse_ts(getattr(r, "_captured_at", "") or "")
        # EvidenceResult lacks timestamps; derive from span recency via
        # video_id lookup would add a join — use published proxy: the
        # caller can re-check via authority. Here we report candidates
        # with reopen provenance and let /wiki compare timestamps it
        # fetches (authority_ref exposed).
        # M-gate #8: reopen captured_at from authority (canonical source)
        # via catalog authority_ref — verified reliable; no contract change
        cap = _captured_at(r.eu_id)
        newer.append({
            "role": "staleness_review_candidate",
            "video_id": r.video_id, "eu_id": r.eu_id,
            "source_url": r.url, "char_span": [r.start_char, r.end_char],
            "evidence_text": r.snippet[:300], "rank": res.index(r) + 1,
            "captured_at": cap,
            "newer_than_last_verified": (
                (_parse_ts(cap) and lv) and _parse_ts(cap) > lv) or None,
        })
    # M-gate #7: freshness precondition — absence is NOT evidence while
    # the index is materially behind the authority
    from ef import freshness as _fr
    try:
        lag_now = _fr.compute_lag(
            _fr.load_state().get("indexed_watermark", ""))["index_lag_count"]
    except Exception:
        lag_now = -1                       # unknown -> conservative
    FRESH_LAG_MAX = 1000                   # operational contract threshold
    fresh_enough = 0 <= lag_now <= FRESH_LAG_MAX

    if lv is None:
        signal = "unknown_last_verified"
    elif not fresh_enough:
        signal = "freshness_incomplete"    # no_new_evidence FORBIDDEN
    else:
        signal = ("newer_material_candidate_needs_review" if newer
                  else "no_new_evidence")
    return {"mode": "wiki_staleness", "claim": claim,
            "last_verified": last_verified, "signal": signal,
            "ef_lag_at_query": lag_now,
            "ef_fresh_enough": fresh_enough,
            "note": "signal = review eligibility, NOT staleness judgment; "
                    "/wiki reopens candidates (provenance retained) and "
                    "compares captured_at against last_verified",
            "candidates": newer}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ef-wiki-maintenance")
    ap.add_argument("mode", choices=("evidence", "contradiction", "staleness"))
    ap.add_argument("claim")
    ap.add_argument("--last-verified", default=None,
                    help="wiki page last_verified ISO timestamp")
    ap.add_argument("--existing-sources", nargs="*", default=[],
                    help="channel ids/titles already backing the claim")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--format", choices=("json", "text"), default="json")
    a = ap.parse_args(argv)

    from ef import readiness
    st = readiness.get_state()
    if st.get("state") not in ("ready", "unknown"):
        out = {"status": "unavailable", "readiness": st.get("state"),
               "candidates": []}
        print(json.dumps(out))
        return 0

    from ef import embedding, buildspec
    from ef.query_server import ProductionQuery
    pq = ProductionQuery(embedding.BGEM3Dual(),
                         generation=buildspec.active_generation())
    if a.mode == "evidence":
        out = mode_evidence(pq, a.claim, a.top_k)
    elif a.mode == "contradiction":
        out = mode_contradiction(pq, a.claim, a.existing_sources, a.top_k)
    else:
        out = mode_staleness(pq, a.claim, a.last_verified or "", a.top_k)
    out["status"] = "ok"
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
