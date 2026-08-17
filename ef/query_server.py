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
# Selected by the v3 routing comparison (routing_dev_results.json):
# D_weighted won on df2-10 tie ranking (R@1 0.533 vs C 0.467); all of
# B/C/D restore the deterministic df=1 property (R@1 = 1.0).
DEFAULT_POLICY = "D_weighted"


class ProductionQuery:
    def __init__(self, encoder, generation: int, snippet_context: int = 120,
                 policy: str | None = None):
        self.encoder = encoder
        self.generation = generation
        self.snippet_context = snippet_context
        self.collection = ps.collection_name(generation)
        self.policy = policy or DEFAULT_POLICY
        self._fts = None
        self._ro_conn = None   # persistent authority reader: per-call
        self._cat_conn = None  # persistent catalog reader
        # connects cost ~1.4s against the 1.4GB WAL db (measured,
        # same_corpus_baselines.json hydration_reopen_ms)

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

    def _reopen(self, eu_id: str, start: int, end: int) -> str:
        """Snippet via PK lookups only: catalog eu_id -> authority_ref
        (== transcripts.sqlite cache_key PK) -> substr. A video_id lookup
        would full-scan the 1.4GB authority (no index on video_id)."""
        import sqlite3
        from . import catalog as _catalog
        if self._cat_conn is None:
            self._cat_conn = _catalog.connect()
        if self._ro_conn is None:
            from .authority import TRANSCRIPTS_DB
            self._ro_conn = sqlite3.connect(
                f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
        cache_key = self._cat_conn.execute(
            "select authority_ref from eu where eu_id=?", (eu_id,)).fetchone()
        if cache_key is None:
            raise LookupError(f"eu {eu_id} not in catalog")
        lo = max(0, start - self.snippet_context)
        row = self._ro_conn.execute(
            "select substr(transcript, ?, ?) from transcript_cache "
            "where cache_key = ?",
            (lo + 1, (end + self.snippet_context) - lo, cache_key[0])).fetchone()
        if row is None or row[0] is None:
            raise LookupError(f"authority row missing for {cache_key[0]}")
        return row[0]

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
        elif route.intent == "exact_strict":
            # literal only, no semantic fill (D-gate rule 3)
            fts_ids = self._fts_lane(query_text, top=max(limit * 5, 50))
            by_id: dict = {}
            if fts_ids:
                extra = qc.retrieve(self.collection,
                                    ids=[ps.point_id(c) for c in fts_ids[:limit]],
                                    with_payload=True)
                by_id = {p.payload["chunk_id"]: p for p in extra}
            final = [(by_id[c], 1.0 / (i + 1)) for i, c in
                     enumerate(fts_ids[:limit]) if c in by_id]
            exact_hit = True
        else:  # identifier: containment priority at any df (D-gate rule 2)
            fts_ids = self._fts_lane(query_text, top=100)
            if not fts_ids:
                # D-gate b-prime rule 1: zero literal matches => PRIMARY
                # EVIDENCE EMPTY. A semantic near-twin must not masquerade
                # as evidence for identifier intent.
                final = []
            else:
                points, _ = self._semantic_legs(query_text, flt)
                sem_ids = [p.payload["chunk_id"] for p in points]
                fused = routing.fuse_identifier_priority(fts_ids, sem_ids, limit)
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
            snippet = self._reopen(pl["eu_id"], pl["start_char"],
                                   pl["end_char"])
            out.append(EvidenceResult(
                chunk_id=pl["chunk_id"], eu_id=pl["eu_id"],
                video_id=pl["video_id"], title=pl["title"] or "",
                channel_id=pl["channel_id"],
                channel_title=pl["channel_title"] or "",
                url=f"https://youtu.be/{pl['video_id']}",
                start_char=pl["start_char"], end_char=pl["end_char"],
                score=float(_s), retrieval_paths=paths, snippet=snippet))
        return out
