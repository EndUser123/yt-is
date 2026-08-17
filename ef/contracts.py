"""Evidence Fabric contracts: EvidenceUnit, ChunkRecord, EvidenceResult.

Authority/retrieval separation (amendment v1.1 §4):
- EvidenceUnit is the authority-layer record: one per authoritative datum
  (here: one cached transcript per video). It never contains derived state.
- ChunkRecord is the projection-layer input: an addressable span of an EU
  with char-offset provenance (D005: transcripts carry no timestamps).
- EvidenceResult is what every consumer receives: a scored, addressable
  answer that can be reopened against the authority layer.

These dataclasses are the production contract for Phase A-0 onward; the smoke
test exercises exactly these objects (amendment: A-0 is non-throwaway).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# Media kinds (extensible: visual pipeline adds frame/ocr kinds later)
MEDIA_TRANSCRIPT = "transcript"

# Retrieval path tags recorded on an EvidenceResult
PATH_DENSE = "dense"
PATH_SPARSE = "sparse"
PATH_FUSED = "fused"


def _req(value: Any, name: str) -> Any:
    if value is None or value == "":
        raise ValueError(f"contract violation: {name} is required")
    return value


@dataclass(frozen=True)
class EvidenceUnit:
    """Authority record: one authoritative transcript (or future media kind)."""

    eu_id: str                 # f"{video_id}:{media_kind}"
    media_kind: str            # MEDIA_TRANSCRIPT
    video_id: str
    channel_id: str
    channel_title: str
    title: str
    lang: str
    source: str                # fetching tool that produced the datum
    authority_ref: str         # cache_key in the authority DB
    content_hash: str          # sha256 of transcript text
    captured_at: str           # ISO timestamp from authority DB
    published_at: str          # video publish date ("" when unknown)
    duration_s: int            # 0 when unknown
    char_length: int

    def validate(self) -> "EvidenceUnit":
        _req(self.eu_id, "eu_id")
        _req(self.video_id, "video_id")
        _req(self.media_kind, "media_kind")
        if self.eu_id != f"{self.video_id}:{self.media_kind}":
            raise ValueError(f"eu_id must be video_id:media_kind, got {self.eu_id}")
        if self.char_length <= 0:
            raise ValueError("char_length must be positive")
        if self.media_kind != MEDIA_TRANSCRIPT:
            raise ValueError(f"unknown media_kind {self.media_kind!r}")
        return self

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChunkRecord:
    """Addressable span of an EU — the unit that gets embedded and indexed."""

    chunk_id: str              # f"{eu_id}#{ordinal:05d}"
    eu_id: str
    ordinal: int
    start_char: int            # inclusive offset into authority transcript
    end_char: int              # exclusive offset into authority transcript
    text: str
    approx_tokens: int

    def validate(self) -> "ChunkRecord":
        if self.chunk_id != f"{self.eu_id}#{self.ordinal:05d}":
            raise ValueError(f"chunk_id must be eu_id#ordinal, got {self.chunk_id}")
        if self.start_char < 0 or self.end_char <= self.start_char:
            raise ValueError(f"invalid span [{self.start_char},{self.end_char})")
        if self.approx_tokens <= 0:
            raise ValueError("approx_tokens must be positive")
        return self


@dataclass(frozen=True)
class EvidenceResult:
    """What consumers get: scored, reopen-able evidence."""

    chunk_id: str
    eu_id: str
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    url: str                   # https://youtu.be/{video_id}
    start_char: int
    end_char: int
    score: float
    retrieval_paths: tuple[str, ...]   # which paths contributed (dense/sparse/fused)
    snippet: str               # reopened authority text at the span ± context

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["retrieval_paths"] = list(self.retrieval_paths)
        return d


@dataclass
class SmokeReceipt:
    """Receipt structure for the A-0 smoke (written verbatim to docs/)."""

    ran_at: str
    config: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    timings_s: dict[str, float] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False

    def check(self, name: str, passed: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(passed), "detail": detail})
        return passed
