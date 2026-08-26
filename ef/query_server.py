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
from .query import external_url

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
        # Per-THREAD readers: the warm service is a ThreadingHTTPServer, so
        # cached sqlite connections must never cross threads (SQLite
        # thread-affinity). Each worker thread pays the connect cost once
        # (~1.4s against the 1.4GB WAL db, measured,
        # same_corpus_baselines.json hydration_reopen_ms).
        import threading
        self._tls = threading.local()
        # Encoder lock: BGE-M3 (PyTorch) is not documented thread-safe for
        # concurrent inference. In the merged service (MCP face daemon
        # thread + ThreadingHTTPServer worker threads), unsynchronized
        # concurrent encode calls corrupt the output vectors (observed as
        # Qdrant 400 "expected some form of vector" at a fixed column —
        # the first NaN/garbage position in the dense vector). Serialize
        # all encode access behind one lock.
        self._encode_lock = threading.Lock()

    def _thread_state(self):
        st = self._tls.__dict__
        if "fts" not in st:
            st["fts"] = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True)
        if "cat" not in st:
            from . import catalog as _catalog
            st["cat"] = _catalog.connect()
        if "ro" not in st:
            from .authority import TRANSCRIPTS_DB
            st["ro"] = sqlite3.connect(
                f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
        return st

    # ---- leg builders -------------------------------------------------

    def _semantic_legs(self, query_text: str, flt, qc=None) -> tuple[list, list]:
        """Server-side hybrid (dense + learned sparse, RRF) -> point list,
        and the raw dense/sparse fused ids (positions imply both legs)."""
        qv, qlw = self._encode(query_text)
        idxs = sorted(qlw.keys())
        # both prefetch legs take the channel filter: unfiltered legs fill
        # the fusion pool with other channels' points and the post-fusion
        # query_filter then shrinks channel-restricted results (same defect
        # fixed in ef.query.HybridQuery)
        prefetch = [
            models.Prefetch(query=[float(x) for x in qv],
                            using=ps.DENSE_NAME, limit=100, filter=flt),
            models.Prefetch(query=models.SparseVector(
                indices=[int(t) for t in idxs],
                values=[float(qlw[t]) for t in idxs]),
                using=ps.LEX_NAME, limit=100, filter=flt),
        ]
        res = (qc or server.client()).query_points(
            collection_name=self.collection, prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=100, query_filter=flt, with_payload=True)
        return list(res.points), None

    def _encode(self, text: str):
        with self._encode_lock:
            dense, lex = self.encoder.encode([text])
        return dense[0], lex[0]

    def _fts_lane(self, query_text: str, top: int = 100) -> list[str]:
        fts = self._thread_state()["fts"]
        match = routing.sanitize_fts_query(query_text)
        if not match:
            return []
        rows = fts.execute(
            "select chunk_id from chunks where chunks match ? "
            "order by bm25(chunks) limit ?", (match, top)).fetchall()
        return [r[0] for r in rows]

    FTS_CHANNEL_SCAN_CAP = 1000   # bounded overfetch for filtered lanes
    FTS_CHANNEL_BATCH = 200

    def _fts_lane_filtered(self, query_text: str, limit: int,
                           channel_id: str, qc) -> list[str]:
        """Channel-restricted BM25 candidates, filtered BEFORE truncation.

        The FTS index carries only (text, chunk_id) — no channel column —
        so restriction resolves chunk payloads via qdrant retrieve in
        bounded batches over the BM25-ordered candidate list, until
        `limit` channel matches are collected or the scan cap is hit.
        Without this, channel-filtered exact/identifier/ambiguous queries
        truncate to the global top-N first and drop every row whose
        channel mismatches afterwards (the underfill defect: 0 of 8
        results for a channel holding 10 matches).
        """
        ids = self._fts_lane(query_text, top=self.FTS_CHANNEL_SCAN_CAP)
        out: list[str] = []
        for i in range(0, len(ids), self.FTS_CHANNEL_BATCH):
            batch = ids[i:i + self.FTS_CHANNEL_BATCH]
            points = qc.retrieve(
                self.collection,
                ids=[ps.point_id(c) for c in batch],
                with_payload=True)
            for p in points:
                if p.payload.get("channel_id") == channel_id:
                    out.append(p.payload["chunk_id"])
                    if len(out) >= limit:
                        return out
        return out

    def _reopen(self, eu_id: str, start: int, end: int) -> str:
        """Snippet via PK lookups only: catalog eu_id -> authority_ref
        (== transcripts.sqlite cache_key PK) -> substr. A video_id lookup
        would full-scan the 1.4GB authority (no index on video_id)."""
        st = self._thread_state()
        cache_key = st["cat"].execute(
            "select authority_ref from eu where eu_id=?", (eu_id,)).fetchone()
        if cache_key is None:
            raise LookupError(f"eu {eu_id} not in catalog")
        lo = max(0, start - self.snippet_context)
        row = st["ro"].execute(
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

        if route.intent == "ambiguous":
            # F-gate: dual retrieval, ambiguity-aware merge (policy D).
            # Literal subgroup ranked semantically, weighted leg; unique
            # literals still outrank semantic-only hits; no false pins.
            fts_ids = (self._fts_lane_filtered(query_text, 100, channel_id, qc)
                       if channel_id
                       else self._fts_lane(query_text, top=100))
            if not fts_ids:
                final = []      # no literal AND weak token: no pin (cf. zero-literal rule)
                exact_hit = True
            else:
                points, _ = self._semantic_legs(query_text, flt, qc)
                sem_ids = [p.payload["chunk_id"] for p in points]
                fused = routing.fuse_ambiguous_subgroup(fts_ids, sem_ids, limit)
                lit = set(fts_ids)
                by_id = {p.payload["chunk_id"]: p for p in points}
                missing = [c for c, _ in fused if c not in by_id]
                if missing:
                    extra = qc.retrieve(self.collection,
                                        ids=[ps.point_id(c) for c in missing],
                                        with_payload=True)
                    for p in extra:
                        by_id[p.payload["chunk_id"]] = p
                final = [(by_id[c], 1.0 / (i + 1)) for i, (c, _l) in
                         enumerate(fused) if c in by_id]
                exact_hit = any(l for _c, l in fused[:1])
        elif route.intent == "comparison":
            # G-gate: class-specific sparse-heavier fusion (dev-measured
            # best: any@3 0.90 / nDCG@3 0.853 vs production 0.833/0.747).
            qv, lw = self._encode(query_text)
            idxs = sorted(lw.keys())
            d_leg = qc.query_points(
                self.collection, query=[float(x) for x in qv],
                using=ps.DENSE_NAME, limit=100, with_payload=True,
                query_filter=flt).points
            s_leg = qc.query_points(
                self.collection, query=models.SparseVector(
                    indices=[int(t) for t in idxs],
                    values=[float(lw[t]) for t in idxs]),
                using=ps.LEX_NAME, limit=100, with_payload=True,
                query_filter=flt).points
            score = {}
            for rk, p_ in enumerate(d_leg):
                cid = p_.payload["chunk_id"]
                score[cid] = score.get(cid, 0.0) + 1.0 / (60 + rk + 1)
            for rk, p_ in enumerate(s_leg):
                cid = p_.payload["chunk_id"]
                score[cid] = score.get(cid, 0.0) + 2.0 / (60 + rk + 1)
            by_id = {p_.payload["chunk_id"]: p_ for p_ in list(d_leg) + list(s_leg)}
            fused = sorted(score.items(), key=lambda kv: -kv[1])[:limit]
            final = [(by_id[c], s) for c, s in fused if c in by_id]
            exact_hit = False
        elif route.intent == "semantic":
            points, _ = self._semantic_legs(query_text, flt, qc)
            final = [(p, p.score) for p in points[:limit]]
            exact_hit = False
        elif route.intent == "exact_strict":
            # literal only, no semantic fill (D-gate rule 3); with a
            # channel filter the lane restricts BEFORE truncation
            fts_ids = (self._fts_lane_filtered(query_text, max(limit * 5, 50),
                                               channel_id, qc)
                       if channel_id
                       else self._fts_lane(query_text, top=max(limit * 5, 50)))
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
            fts_ids = (self._fts_lane_filtered(query_text, 100, channel_id, qc)
                       if channel_id
                       else self._fts_lane(query_text, top=100))
            if not fts_ids:
                # D-gate b-prime rule 1: zero literal matches => PRIMARY
                # EVIDENCE EMPTY. A semantic near-twin must not masquerade
                # as evidence for identifier intent.
                final = []
            else:
                points, _ = self._semantic_legs(query_text, flt, qc)
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
            try:
                snippet = self._reopen(pl["eu_id"], pl["start_char"],
                                       pl["end_char"])
            except LookupError:
                # Non-transcript sources (reddit/RSS/HN/discord) have EU
                # entries + chunks in the catalog but their content lives
                # outside transcript_cache (connector ingest path). Return
                # metadata-only snippet; the result is still valid.
                snippet = f"[{pl.get('source', 'non-transcript')}] {pl.get('title', '')[:200]}"
            out.append(EvidenceResult(
                chunk_id=pl["chunk_id"], eu_id=pl["eu_id"],
                video_id=pl["video_id"], title=pl["title"] or "",
                channel_id=pl["channel_id"],
                channel_title=pl["channel_title"] or "",
                url=external_url(pl["video_id"], pl["channel_id"]),
                start_char=pl["start_char"], end_char=pl["end_char"],
                score=float(_s), retrieval_paths=paths, snippet=snippet))
        return out
