"""Projection: Qdrant local-mode collection holding chunk vectors + payload.

Rebuildable derived state (amendment §3 invariants): deleting this directory
and rebuilding from authority + catalog loses nothing. Local mode holds an
exclusive lock on its path (D008): one writer process at a time — the smoke
and future builds are single-writer by contract.

Payload kept minimal + filterable: eu_id, video_id, channel_id, ordinal,
start/end char, title. Text is NOT stored here — reopen from authority.
"""

from __future__ import annotations

from pathlib import Path

from qdrant_client import QdrantClient, models

from .contracts import ChunkRecord

EF_DATA = Path("P:/.data/yt-is/ef")
QDRANT_DIR = EF_DATA / "qdrant_local"

COLLECTION = "evidence_chunks"
DENSE_NAME = "dense"
SPARSE_NAME = "lex"


def connect(path: Path = QDRANT_DIR) -> QdrantClient:
    path.parent.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))


def ensure_collection(client: QdrantClient, dense_dim: int,
                      recreate: bool = False) -> None:
    if recreate and client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config={
                DENSE_NAME: models.VectorParams(
                    size=dense_dim, distance=models.Distance.COSINE,
                    on_disk=False),
            },
            sparse_vectors_config={
                SPARSE_NAME: models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False)),
            },
        )


def upsert_chunks(client: QdrantClient, chunks: list[ChunkRecord],
                  dense_vectors: list[list[float]],
                  sparse_vectors: list[tuple[list[int], list[float]]],
                  eu_meta: dict[str, dict]) -> None:
    """eu_meta: eu_id -> {video_id, channel_id, title} for payload."""
    from hashlib import md5

    def pid(chunk_id: str) -> int:
        return int.from_bytes(md5(chunk_id.encode("utf-8")).digest()[:8], "big")

    points = []
    for ch, dv, (sidx, sval) in zip(chunks, dense_vectors, sparse_vectors):
        meta = eu_meta[ch.eu_id]
        points.append(models.PointStruct(
            id=pid(ch.chunk_id),
            vector={DENSE_NAME: dv, SPARSE_NAME: models.SparseVector(
                indices=sidx, values=sval)},
            payload={
                "chunk_id": ch.chunk_id,
                "eu_id": ch.eu_id,
                "video_id": meta["video_id"],
                "channel_id": meta["channel_id"],
                "channel_title": meta["channel_title"],
                "title": meta["title"],
                "ordinal": ch.ordinal,
                "start_char": ch.start_char,
                "end_char": ch.end_char,
            },
        ))
    client.upsert(collection_name=COLLECTION, points=points)


def count(client: QdrantClient) -> int:
    return client.count(COLLECTION, exact=True).count


def drop_all(path: Path = QDRANT_DIR) -> None:
    """Full reset of the local projection (rebuildable by definition)."""
    import shutil
    if path.exists():
        shutil.rmtree(path)
