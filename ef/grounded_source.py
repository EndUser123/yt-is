"""Typed transport contract between source analysis and the intelligence layer.

The ``distill-source`` skill is the source-faithful authoring procedure.  This
module is its runtime boundary: it carries the representation that was
actually inspected into downstream inference without pretending that a
transcript proves frame-, audio-, or full-media facts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import urlparse


ARTIFACT_VERSION = "grounded-source-v1"
_STATUSES = frozenset({"complete", "partial", "unknown", "unavailable"})
_REPRESENTATIONS = frozenset({"metadata", "transcript", "captions", "audio", "frames", "full_media"})
_ROLES = frozenset({"primary", "official_metadata", "attributed_third_party"})


@dataclass(frozen=True, slots=True)
class GroundedSpan:
    """A source span with only the locator precision actually available."""

    text: str
    locator: str | None = None
    representation: str = "transcript"
    source_role: str = "primary"


@dataclass(frozen=True, slots=True)
class GroundedSourceArtifact:
    """A provenance-preserving source representation for downstream IL use."""

    source_id: str
    source_url: str | None
    title: str
    source_status: str
    representations: tuple[str, ...]
    transcript_language: str | None
    transcript_kind: str | None
    spans: tuple[GroundedSpan, ...]
    retrieval: tuple[tuple[str, str], ...]
    source_hash: str
    evidence_cluster_ids: tuple[int, ...] = ()
    artifact_version: str = ARTIFACT_VERSION

    @property
    def source_text(self) -> str:
        """Return the inspected textual representation in source order."""
        return "\n\n".join(span.text for span in self.spans if span.text)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "representations": list(self.representations),
            "spans": [asdict(span) for span in self.spans],
            "retrieval": {key: value for key, value in self.retrieval},
            "evidence_cluster_ids": list(self.evidence_cluster_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GroundedSourceArtifact":
        if not isinstance(value, dict):
            raise ValueError("grounded source artifact must be an object")
        raw_spans = value.get("spans", [])
        if not isinstance(raw_spans, list):
            raise ValueError("grounded source spans must be a list")
        if any(not isinstance(span, dict) for span in raw_spans):
            raise ValueError("grounded source spans must contain objects")
        try:
            spans = tuple(GroundedSpan(**span) for span in raw_spans)
        except (TypeError, ValueError) as exc:
            raise ValueError("grounded source contains an invalid span") from exc
        retrieval = value.get("retrieval", {})
        if not isinstance(retrieval, dict):
            raise ValueError("grounded source retrieval must be an object")
        if any(not isinstance(key, str) or not isinstance(item, str)
               for key, item in retrieval.items()):
            raise ValueError("grounded source retrieval keys and values must be strings")
        retrieval_pairs = tuple(sorted(retrieval.items()))
        raw_representations = value.get("representations", [])
        if not isinstance(raw_representations, list):
            raise ValueError("grounded source representations must be a list")
        raw_cluster_ids = value.get("evidence_cluster_ids", [])
        if not isinstance(raw_cluster_ids, list):
            raise ValueError("grounded source cluster ids must be a list")
        if any(type(cid) is not int for cid in raw_cluster_ids):
            raise ValueError("grounded source cluster ids must be integers")
        source_id = value.get("source_id", "")
        source_url = value.get("source_url")
        title = value.get("title", "Unknown")
        if not isinstance(source_id, str) or not isinstance(title, str):
            raise ValueError("grounded source id and title must be strings")
        if source_url is not None and not isinstance(source_url, str):
            raise ValueError("grounded source URL must be a string or null")
        artifact = cls(
            source_id=source_id,
            source_url=source_url,
            title=title,
            source_status=str(value.get("source_status", "unknown")),
            representations=tuple(raw_representations),
            transcript_language=value.get("transcript_language"),
            transcript_kind=value.get("transcript_kind"),
            spans=spans,
            retrieval=retrieval_pairs,
            source_hash=str(value.get("source_hash", "")),
            evidence_cluster_ids=tuple(raw_cluster_ids),
            artifact_version=str(value.get("artifact_version", ARTIFACT_VERSION)),
        )
        validate_artifact(artifact)
        return artifact


def _content_hash(
    source_id: str,
    title: str,
    representations: tuple[str, ...],
    transcript_language: str | None,
    transcript_kind: str | None,
    spans: tuple[GroundedSpan, ...],
) -> str:
    payload = {
        "source_id": source_id,
        "title": title,
        "representations": list(representations),
        "transcript_language": transcript_language,
        "transcript_kind": transcript_kind,
        "spans": [asdict(span) for span in spans],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_artifact(artifact: GroundedSourceArtifact) -> None:
    """Fail closed on malformed or provenance-inconsistent artifacts."""
    if not isinstance(artifact, GroundedSourceArtifact):
        raise ValueError("grounded source must be a GroundedSourceArtifact")
    if not isinstance(artifact.source_id, str) or not isinstance(artifact.title, str):
        raise ValueError("grounded source id and title must be strings")
    if artifact.source_url is not None and not isinstance(artifact.source_url, str):
        raise ValueError("grounded source URL must be a string or null")
    if artifact.transcript_language is not None and not isinstance(
            artifact.transcript_language, str):
        raise ValueError("grounded source transcript language must be a string or null")
    if artifact.transcript_kind is not None and not isinstance(
            artifact.transcript_kind, str):
        raise ValueError("grounded source transcript kind must be a string or null")
    if not isinstance(artifact.source_hash, str):
        raise ValueError("grounded source hash must be a string")
    if not artifact.source_id.strip():
        raise ValueError("grounded source source_id is required")
    if artifact.source_status not in _STATUSES:
        raise ValueError(f"invalid grounded source status: {artifact.source_status!r}")
    if artifact.artifact_version != ARTIFACT_VERSION:
        raise ValueError(f"unsupported grounded source version: {artifact.artifact_version!r}")
    if artifact.source_url:
        parsed = urlparse(artifact.source_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("grounded source URL must use http or https")
    if any(not isinstance(rep, str) or rep not in _REPRESENTATIONS
           for rep in artifact.representations):
        raise ValueError("grounded source contains an unknown representation")
    if artifact.source_status == "unavailable" and artifact.spans:
        raise ValueError("unavailable grounded source cannot contain spans")
    if artifact.source_status != "unavailable" and not artifact.spans:
        raise ValueError("available grounded source must contain at least one span")
    for span in artifact.spans:
        if not isinstance(span.text, str) or (
                span.locator is not None and not isinstance(span.locator, str)):
            raise ValueError("grounded source span text and locator must be strings")
        if not span.text.strip():
            raise ValueError("grounded source spans must contain text")
        if (not isinstance(span.representation, str)
                or span.representation not in _REPRESENTATIONS):
            raise ValueError(f"unknown span representation: {span.representation!r}")
        if span.representation not in artifact.representations:
            raise ValueError(
                "grounded source span representation is not declared"
            )
        if (not isinstance(span.source_role, str)
                or span.source_role not in _ROLES):
            raise ValueError(f"unknown span source role: {span.source_role!r}")
    if any(not isinstance(pair, tuple) or len(pair) != 2
           or not isinstance(pair[0], str) or not isinstance(pair[1], str)
           for pair in artifact.retrieval):
        raise ValueError("grounded source retrieval must contain string pairs")
    if not isinstance(artifact.evidence_cluster_ids, tuple):
        raise ValueError("grounded source cluster ids must be a tuple")
    if any(type(cid) is not int or cid < 0 for cid in artifact.evidence_cluster_ids):
        raise ValueError("evidence cluster ids must be non-negative integers")
    expected = _content_hash(
        artifact.source_id,
        artifact.title,
        artifact.representations,
        artifact.transcript_language,
        artifact.transcript_kind,
        artifact.spans,
    )
    if artifact.source_hash != expected:
        raise ValueError("grounded source hash does not match its content")


def bind_evidence_clusters(
    artifact: GroundedSourceArtifact,
    cluster_ids,
) -> GroundedSourceArtifact:
    """Return the same artifact with explicit cluster provenance attached."""
    cluster_ids = tuple(cluster_ids)
    if any(type(cid) is not int or cid < 0 for cid in cluster_ids):
        raise ValueError("evidence cluster ids must be non-negative integers")
    merged = tuple(sorted(set(artifact.evidence_cluster_ids) | set(cluster_ids)))
    bound = replace(artifact, evidence_cluster_ids=merged)
    validate_artifact(bound)
    return bound


def to_inference_context(
    artifact: GroundedSourceArtifact,
    *,
    max_chars: int = 80_000,
) -> str:
    """Render an artifact as bounded, provenance-labelled IL prompt context.

    The function refuses to truncate. Callers must chunk a larger source and
    preserve all chunks if the configured bound is exceeded.
    """
    validate_artifact(artifact)
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    header = [
        f"SOURCE_ID: {artifact.source_id}",
        f"SOURCE_HASH: {artifact.source_hash}",
        f"SOURCE_STATUS: {artifact.source_status}",
        f"REPRESENTATIONS: {', '.join(artifact.representations) or 'none'}",
        "EVIDENCE_CLUSTER_IDS: "
        f"{', '.join(str(cid) for cid in artifact.evidence_cluster_ids) or 'none'}",
        f"TRANSCRIPT_LANGUAGE: {artifact.transcript_language or 'unknown'}",
        f"TRANSCRIPT_KIND: {artifact.transcript_kind or 'unknown'}",
    ]
    if artifact.source_url:
        header.append(f"SOURCE_URL: {artifact.source_url}")
    body = []
    for span in artifact.spans:
        locator = f"[{span.locator}] " if span.locator else ""
        body.append(f"{locator}{span.text}")
    rendered = "\n".join(header + ["SOURCE_SPANS:"] + body)
    if len(rendered) > max_chars:
        raise ValueError(
            f"grounded source context exceeds {max_chars} characters; "
            "chunk the complete artifact instead of truncating it"
        )
    return rendered


def from_transcript_result(
    video_id: str,
    video_url: str,
    result: Any,
    *,
    title: str | None = None,
) -> GroundedSourceArtifact:
    """Build an artifact from the transcript chain's actual result.

    A successful fetch establishes availability, not completeness.  Therefore
    the default status is ``unknown`` until a caller has an explicit coverage
    receipt from the source transport.
    """
    transcript = str(getattr(result, "transcript", "") or "")
    actual_title = title or getattr(result, "video_title", None) or "Unknown"
    if not transcript.strip():
        source_id = video_id
        representations: tuple[str, ...] = ()
        transcript_language = (
            getattr(result, "raw_lang", None)
            or getattr(result, "detected_lang", None)
        )
        source_hash = _content_hash(
            source_id, actual_title, representations, transcript_language,
            None, (),
        )
        artifact = GroundedSourceArtifact(
            source_id=source_id,
            source_url=video_url,
            title=actual_title,
            source_status="unavailable",
            representations=representations,
            transcript_language=transcript_language,
            transcript_kind=None,
            spans=(),
            retrieval=(
                ("source", str(getattr(result, "source", "none"))),
                ("source_stage", str(getattr(result, "source_stage", ""))),
            ),
            source_hash=source_hash,
            evidence_cluster_ids=(),
        )
        validate_artifact(artifact)
        return artifact

    spans = (GroundedSpan(text=transcript),)
    representations = ("transcript",)
    artifact = GroundedSourceArtifact(
        source_id=video_id,
        source_url=video_url,
        title=actual_title,
        source_status="unknown",
        representations=representations,
        transcript_language=(getattr(result, "raw_lang", None)
                             or getattr(result, "detected_lang", None)
                             or getattr(result, "lang", None)),
        transcript_kind=None,
        spans=spans,
        retrieval=(
            ("source", str(getattr(result, "source", "unknown"))),
            ("source_stage", str(getattr(result, "source_stage", ""))),
            ("translated", str(bool(getattr(result, "was_translated", False)))),
        ),
        source_hash=_content_hash(
            video_id, actual_title, representations,
            (getattr(result, "raw_lang", None)
             or getattr(result, "detected_lang", None)
             or getattr(result, "lang", None)),
            None,
            spans,
        ),
        evidence_cluster_ids=(),
    )
    validate_artifact(artifact)
    return artifact
