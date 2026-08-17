"""Server-mode projection: gen-N collections on the yt-is qdrant server.

Production path per C-gate: collection evidence_chunks__genN with named
dense vector (HNSW m=32) + named learned-sparse vector; payload indexes on
channel_id / video_id. The embedded/local-mode functions in projection.py
remain for benchmark-scale tooling; this module is the canonical path.
"""

from __future__ import annotations

from hashlib import md5

from qdrant_client import models

from . import server
from .contracts import ChunkRecord

DENSE_NAME = "dense"
LEX_NAME = "lex"          # bge-m3 learned sparse


def collection_name(generation: int) -> str:
    return f"evidence_chunks__gen{generation}"


def point_id(chunk_id: str) -> int:
    return int.from_bytes(md5(chunk_id.encode("utf-8")).digest()[:8], "big")


def ensure_collection(generation: int, dense_dim: int = 1024,
                      hnsw_m: int = 32, recreate: bool = False):
    qc = server.client()
    coll = collection_name(generation)
    if recreate and qc.collection_exists(coll):
        qc.delete_collection(coll)
    if not qc.collection_exists(coll):
        qc.create_collection(
            collection_name=coll,
            vectors_config={DENSE_NAME: models.VectorParams(
                size=dense_dim, distance=models.Distance.COSINE,
                hnsw_config=models.HnswConfigDiff(m=hnsw_m))},
            sparse_vectors_config={
                LEX_NAME: models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False))})
        qc.create_payload_index(coll, "channel_id",
                                models.PayloadSchemaType.KEYWORD)
        qc.create_payload_index(coll, "video_id",
                                models.PayloadSchemaType.KEYWORD)
    return qc


def upsert_chunks(qc, chunks: list[ChunkRecord],
                  dense_vectors: list[list[float]],
                  lex_weights: list[dict[int, float]],
                  eu_meta: dict[str, dict], generation: int,
                  batch: int = 500) -> int:
    coll = collection_name(generation)
    n = 0
    for i in range(0, len(chunks), batch):
        pts = []
        for ch, dv, lw in zip(chunks[i:i + batch], dense_vectors[i:i + batch],
                              lex_weights[i:i + batch]):
            meta = eu_meta[ch.eu_id]
            idxs = sorted(lw.keys())
            pts.append(models.PointStruct(
                id=point_id(ch.chunk_id),
                vector={DENSE_NAME: [float(x) for x in dv],
                        LEX_NAME: models.SparseVector(
                            indices=[int(t) for t in idxs],
                            values=[float(lw[t]) for t in idxs])},
                payload={
                    "chunk_id": ch.chunk_id, "eu_id": ch.eu_id,
                    "video_id": meta["video_id"],
                    "channel_id": meta["channel_id"],
                    "channel_title": meta["channel_title"],
                    "title": meta["title"],
                    "metadata_state": meta.get("metadata_state", "complete"),
                    "ordinal": ch.ordinal,
                    "start_char": ch.start_char, "end_char": ch.end_char,
                }))
        qc.upsert(coll, points=pts, wait=True)
        n += len(pts)
    return n


def count(qc, generation: int) -> int:
    return qc.count(collection_name(generation), exact=True).count


def delete_collection(generation: int):
    qc = server.client()
    coll = collection_name(generation)
    if qc.collection_exists(coll):
        qc.delete_collection(coll)
