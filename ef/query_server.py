"""Production query path (C-gate decision 5 + exact-lane decision).

Default hybrid candidate: BGE dense + BGE learned-sparse via Qdrant server
RRF prefetch fusion. FTS5 is NOT in the synchronous semantic path; it is
the EXACT lane (identifier_lanes_dev.json: R@1 0.60 vs bm25 0.40 vs
learned 0.13), engaged only when the query matches an identifier heuristic
so semantic queries never pay for or get diluted by it.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from qdrant_client import models

from . import projection_server as ps
from . import server
from .authority import reopen_span
from .contracts import PATH_DENSE, PATH_FUSED, PATH_SPARSE, EvidenceResult

DEFAULT_LIMIT = 8
RRF_K = 60
FTS_DB = Path("P:/.data/yt-is/ef/fts5.sqlite")

_IDENT = re.compile(
    r"(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[a-z]+(?:\.[a-z0-9]+)+"
    r"|[A-Za-z]*[a-z][A-Z][A-Za-z]*|[A-Z]{3,}[0-9]*"
    r"|[a-zA-Z]+[0-9][a-zA-Z0-9.-]*|\b0x[0-9a-fA-F]+\b)")


def identifier_heuristic(query_text: str) -> bool:
    """True when the query is short and contains exact-token material."""
    words = query_text.split()
    if len(words) > 4:
        return False
    return bool(_IDENT.search(query_text)) or len(words) <= 2


class ProductionQuery:
    def __init__(self, encoder, generation: int, snippet_context: int = 120):
        """encoder: ef.embedding.BGEM3Dual (or compatible: .encode([text])
        -> (dense list, lex list))."""
        self.encoder = encoder
        self.generation = generation
        self.snippet_context = snippet_context
        self.collection = ps.collection_name(generation)
        self._fts = None

    def _encode_query(self, text: str):
        dense, lex = self.encoder.encode([text])
        return dense[0], lex[0]

    def _fts_lane(self, query_text: str, top: int = 100) -> list[str]:
        if self._fts is None:
            self._fts = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True)
        terms = [t.replace('"', "") for t in query_text.split()]
        match = " OR ".join(f'"{t}"' for t in terms if t)
        if not match:
            return []
        rows = self._fts.execute(
            "select chunk_id from chunks where chunks match ? "
            "order by bm25(chunks) limit ?", (match, top)).fetchall()
        return [r[0] for r in rows]

    def relevant(self, query_text: str, limit: int = DEFAULT_LIMIT,
                 channel_id: str | None = None) -> list[EvidenceResult]:
        """RELEVANT primitive: dense+learned RRF; exact FTS5 lane fused in
        Python when the identifier heuristic fires."""
        qv, qlw = self._encode_query(query_text)
        flt = models.Filter(must=[models.FieldCondition(
            key="channel_id", match=models.MatchValue(value=channel_id))]) \
            if channel_id else None
        idxs = sorted(qlw.keys())
        prefetch = [
            models.Prefetch(query=[float(x) for x in qv],
                            using=ps.DENSE_NAME, limit=100),
            models.Prefetch(query=models.SparseVector(
                indices=[int(t) for t in idxs],
                values=[float(qlw[t]) for t in idxs]),
                using=ps.LEX_NAME, limit=100),
        ]
        qc = server.client()
        res = qc.query_points(
            collection_name=self.collection,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=100 if identifier_heuristic(query_text) else limit,
            query_filter=flt, with_payload=True)

        exact = identifier_heuristic(query_text)
        if exact:
            fts_ids = self._fts_lane(query_text)
            score = {p.payload["chunk_id"]: 1.0 / (RRF_K + i + 1)
                     for i, p in enumerate(res.points)}
            for rk, cid in enumerate(fts_ids):
                score[cid] = score.get(cid, 0.0) + 1.0 / (RRF_K + rk + 1)
            by_id = {p.payload["chunk_id"]: p for p in res.points}
            ordered = sorted(score.items(), key=lambda kv: -kv[1])[:limit]
            # FTS-only hits need payload fetch
            missing = [cid for cid, _ in ordered if cid not in by_id]
            if missing:
                extra = qc.retrieve(self.collection,
                                    ids=[ps.point_id(c) for c in missing],
                                    with_payload=True)
                for p in extra:
                    by_id[p.payload["chunk_id"]] = p
            final = [(by_id[cid], s) for cid, s in ordered if cid in by_id]
        else:
            final = [(p, p.score) for p in res.points[:limit]]

        out = []
        for p, _s in final:
            pl = p.payload
            if channel_id and pl["channel_id"] != channel_id:
                continue   # exact-lane hits must respect the filter too
            paths = [PATH_FUSED, PATH_DENSE, PATH_SPARSE] + \
                (["exact_fts5"] if exact else [])
            snippet = reopen_span(pl["video_id"], pl["start_char"],
                                  pl["end_char"],
                                  context=self.snippet_context)
            out.append(EvidenceResult(
                chunk_id=pl["chunk_id"], eu_id=pl["eu_id"],
                video_id=pl["video_id"], title=pl["title"] or "",
                channel_id=pl["channel_id"],
                channel_title=pl["channel_title"] or "",
                url=f"https://youtu.be/{pl['video_id']}",
                start_char=pl["start_char"], end_char=pl["end_char"],
                score=float(_s), retrieval_paths=tuple(paths),
                snippet=snippet))
        return out
