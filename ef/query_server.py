"""Production query path (A" sections 7, 9, 10 + C-gate 5).

Routing-aware: SEMANTIC queries run BGE dense + learned-sparse RRF on the
Qdrant server with NO synchronous FTS5. EXACT queries (identifier-shaped
AND rare, or explicit exact=true / quoted literal) get the FTS5 exact lane
with the selected fusion policy. Common lexical terms and short natural
queries stay SEMANTIC — length never implies identifier intent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from qdrant_client import models

from . import projection_server as ps
from . import routing, server
from .authority import reopen_span
from .contracts import EvidenceResult

DEFAULT_LIMIT = 8
FTS_DB = routing.FTS_DB
# set by the routing-policy comparison (section 9); containment-priority is
# the leading hypothesis until measurement confirms
DEFAULT_POLICY = "C_containment_priority"


class ProductionQuery:
    def __init__(self, encoder, generation: int, snippet_context: int = 120,
                 policy: str | None = None):
        self.encoder = encoder
        self.generation = generation
        self.snippet_context = snippet_context
        self.collection = ps.collection_name(generation)
        self.policy = policy or DEFAULT_POLICY
        self._fts = None

    # ---- leg builders -------------------------------------------------

    def _semantic_legs(self, query_text: str, flt) -> tuple[list, list]:
        """Server-side hybrid (dense + learned sparse, RRF) -> point list,
        and the raw dense/sparse fused ids (positions imply both legs)."""
        qv, qlw = self._encode(query_text)
        idxs = sorted(qlw.keys())
        prefetch = [
            models.Prefetch(query=[float(x) for x in qv],
                            using=ps.DENSE_NAME, limit=100),
            models.Prefetch(query=models.SparseVector(
                indices=[int(t) for t in idxs],
                values=[float(qlw[t]) for t in idxs]),
                using=ps.LEX_NAME, limit=100),
        ]
        res = server.client().query_points(
            collection_name=self.collection, prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=100, query_filter=flt, with_payload=True)
        return list(res.points), None

    def _encode(self, text: str):
        dense, lex = self.encoder.encode([text])
        return dense[0], lex[0]

    def _fts_lane(self, query_text: str, top: int = 100) -> list[str]:
        if self._fts is None:
            self._fts = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True)
        match = routing.sanitize_fts_query(query_text)
        if not match:
            return []
        rows = self._fts.execute(
            "select chunk_id from chunks where chunks match ? "
            "order by bm25(chunks) limit ?", (match, top)).fetchall()
        return [r[0] for r in rows]

    # ---- entry point ---------------------------------------------------

    def relevant(self, query_text: str, limit: int = DEFAULT_LIMIT,
                 channel_id: str | None = None,
                 exact: bool | None = None) -> list[EvidenceResult]:
        route = routing.classify(query_text, exact=exact)
        flt = models.Filter(must=[models.FieldCondition(
            key="channel_id", match=models.MatchValue(value=channel_id))]) \
            if channel_id else None
        qc = server.client()

        if route.intent == "semantic":
            points, _ = self._semantic_legs(query_text, flt)
            final = [(p, p.score) for p in points[:limit]]
            exact_hit = False
        else:
            # exact legs: fts lane + semantic legs as fill candidates
            fts_ids = self._fts_lane(query_text)
            points, _ = self._semantic_legs(query_text, flt)
            sem_ids = [p.payload["chunk_id"] for p in points]
            legs = [sem_ids[:100], fts_ids]
            fused = routing.POLICIES[self.policy](legs, limit, exact_leg_idx=-1)
            by_id = {p.payload["chunk_id"]: p for p in points}
            missing = [c for c in fused if c not in by_id]
            if missing:
                extra = qc.retrieve(self.collection,
                                    ids=[ps.point_id(c) for c in missing],
                                    with_payload=True)
                for p in extra:
                    by_id[p.payload["chunk_id"]] = p
            final = [(by_id[c], 1.0 / (i + 1)) for i, c in enumerate(fused)
                     if c in by_id]
            exact_hit = True

        out = []
        for p, _s in final:
            pl = p.payload
            if channel_id and pl["channel_id"] != channel_id:
                continue   # exact-lane hits respect filters too
            paths = ("fused", "dense", "sparse") + \
                (("exact_fts5",) if exact_hit else ())
            snippet = reopen_span(pl["video_id"], pl["start_char"],
                                  pl["end_char"], context=self.snippet_context)
            out.append(EvidenceResult(
                chunk_id=pl["chunk_id"], eu_id=pl["eu_id"],
                video_id=pl["video_id"], title=pl["title"] or "",
                channel_id=pl["channel_id"],
                channel_title=pl["channel_title"] or "",
                url=f"https://youtu.be/{pl['video_id']}",
                start_char=pl["start_char"], end_char=pl["end_char"],
                score=float(_s), retrieval_paths=paths, snippet=snippet))
        return out
