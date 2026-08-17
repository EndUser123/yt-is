"""BuildSpec: immutable per-generation build contract + atomic promotion.

Phase A findings §7, amended by C-gate: engine = qdrant-server 6390.
- buildspec.json is git-tracked and immutable once the generation build
  starts: any change bumps `generation` (old spec retained in git history).
- promotion.json is the single promotion authority: {"active_generation": N}
  written atomically (tmp file + os.replace).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs" / "evidence-fabric"
SPEC_PATH = DOCS / "buildspec.json"
PROMOTION_PATH = Path("P:/.data/yt-is/ef/promotion.json")

GEN1 = {
    "generation": 1,
    "spec_version": "1.0",
    "authority": {
        "transcripts_db": "P:/.data/yt-is/transcripts.sqlite",
        "status_db": "P:/.data/yt-is/batch_status.sqlite",
        "eligibility": {
            "min_chars": 100,
            "require": ["video_id", "reopenable_transcript"],
            "include_incomplete_metadata": True,
            "exclude_terminal_prefix": "test",
        },
        "quarantine": {
            "rule": "terminal_id LIKE 'test%' OR reopen failure",
            "known": ["dQw4w9WgXcQ"],
        },
    },
    "chunker": {"target_chars": 1100, "overlap_chars": 150, "min_chars": 200},
    "encoder": {
        "model": "BAAI/bge-m3",
        "representations": ["dense", "learned_sparse"],
        "dense_dim": 1024,
        "max_length": 512,
        "fp16": True,
        "query_prefix": "",
    },
    "projection": {
        "engine": "qdrant-server",
        "binary": "P:/.data/yt-is/ef/tools/qdrant.exe",
        "http_port": 6390,
        "grpc_port": 6391,
        "collection": "evidence_chunks__gen1",
        "hnsw_m": 32,
        "payload_indexes": ["channel_id", "video_id"],
    },
    "fusion": {
        "default_prefetch": ["dense", "lex_learned"],
        "exact_lane": "fts5_identifier_heuristic",
        "exact_lane_evidence": {
            "dev_n": 55,
            "fts5": {"r1": 0.6, "r5": 0.8182, "r10": 0.8727, "mrr10": 0.6913},
            "qdrant_bm25": {"r1": 0.4, "r5": 0.6182, "r10": 0.7091, "mrr10": 0.4979},
            "bge_learned_sparse": {"r1": 0.1273, "r5": 0.2545, "r10": 0.3091,
                                    "mrr10": 0.1749},
            "receipt": "docs/evidence-fabric/benchmark/identifier_lanes_dev.json",
        },
        "rrf_k": 60,
    },
}


def write_spec(spec: dict) -> Path:
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPEC_PATH.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    return SPEC_PATH


def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def spec_digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True)
                          .encode()).hexdigest()[:16]


def active_generation() -> int:
    if not PROMOTION_PATH.exists():
        return 0
    return json.loads(PROMOTION_PATH.read_text(encoding="utf-8")) \
        .get("active_generation", 0)


def promote(generation: int, evidence: dict) -> dict:
    """Atomic promotion: tmp write + rename. The single promotion authority."""
    PROMOTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = {"active_generation": generation,
           "promoted_at": __import__("datetime").datetime.now(
               __import__("datetime").timezone.utc).isoformat(),
           "evidence": evidence}
    tmp = PROMOTION_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, PROMOTION_PATH)
    return doc
