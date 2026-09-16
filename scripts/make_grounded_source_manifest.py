"""Build a grounded-source manifest from csf-analyze result JSON files.

The analysis result already carries the typed ``grounded_source`` artifact.
This adapter adds the explicit evidence-cluster associations required by the
bootstrap driver.  Catalog access is read-only and a missing association is a
hard error; a source is never silently broadcast to all clusters.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef.grounded_source import GroundedSourceArtifact, validate_artifact  # noqa: E402
from scripts.build_interest_graph_contract import (  # noqa: E402
    GROUNDED_SOURCE_MANIFEST_VERSION,
)

DEFAULT_CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Publish the manifest only after its complete JSON is durable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise ValueError(f"catalog does not exist: {path}")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _cluster_ids(conn: sqlite3.Connection, video_id: str) -> tuple[int, ...]:
    rows = conn.execute(
        "SELECT DISTINCT cluster_id FROM chunk_clusters "
        "WHERE video_id=? ORDER BY cluster_id", (video_id,)).fetchall()
    return tuple(row[0] for row in rows)


def build_manifest(
    analysis_paths: list[str | Path],
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
) -> dict:
    """Return a validated manifest for analysis result files."""
    by_source: dict[str, tuple[GroundedSourceArtifact, tuple[int, ...]]] = {}
    catalog = Path(catalog_path)
    conn = _read_only_connection(catalog)
    try:
        for raw_path in analysis_paths:
            path = Path(raw_path)
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"could not read analysis result {path}: {exc}") from exc
            if not isinstance(result, dict):
                raise ValueError(f"analysis result {path} must be an object")
            raw_artifact = result.get("grounded_source")
            if not isinstance(raw_artifact, dict):
                raise ValueError(f"analysis result {path} has no grounded_source artifact")
            try:
                artifact = GroundedSourceArtifact.from_dict(raw_artifact)
                validate_artifact(artifact)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"analysis result {path} has an invalid grounded_source: {exc}") from exc
            video_id = result.get("video_id")
            if not isinstance(video_id, str) or not video_id.strip():
                raise ValueError(f"analysis result {path} has no video_id")
            if artifact.source_id != video_id:
                raise ValueError(
                    f"analysis result {path} video_id does not match grounded_source source_id"
                )
            try:
                cluster_ids = _cluster_ids(conn, video_id)
            except sqlite3.Error as exc:
                raise ValueError(
                    f"could not resolve catalog clusters for {video_id!r}: {exc}"
                ) from exc
            if not cluster_ids:
                raise ValueError(
                    f"video {video_id!r} from {path} has no evidence-cluster association"
                )
            if any(type(cluster_id) is not int or cluster_id < 0
                   for cluster_id in cluster_ids):
                raise ValueError(
                    f"catalog returned invalid cluster ids for {video_id!r}"
                )
            prior = by_source.get(artifact.source_id)
            if prior is not None:
                prior_artifact, prior_cluster_ids = prior
                if (prior_artifact.source_hash != artifact.source_hash
                        or prior_artifact.artifact_version != artifact.artifact_version):
                    raise ValueError(
                        f"source id {artifact.source_id!r} appears with conflicting content"
                    )
                if replace(prior_artifact, evidence_cluster_ids=()) != replace(
                        artifact, evidence_cluster_ids=()):
                    raise ValueError(
                        f"source id {artifact.source_id!r} appears with different "
                        "immutable provenance metadata"
                    )
                cluster_ids = tuple(sorted(set(prior_cluster_ids) | set(cluster_ids)))
                artifact = prior_artifact
            # The catalog is the authority for source-to-cluster membership
            # at this boundary. Provider-carried associations are retained in
            # the typed artifact only after being replaced by that read-only
            # catalog result, never unioned as independent authority.
            artifact = replace(artifact, evidence_cluster_ids=tuple(cluster_ids))
            validate_artifact(artifact)
            by_source[artifact.source_id] = (artifact, cluster_ids)
    finally:
        conn.close()

    sources = []
    for source_id in sorted(by_source):
        artifact, cluster_ids = by_source[source_id]
        sources.append({
            "artifact": artifact.to_dict(),
            "cluster_ids": list(cluster_ids),
        })
    return {
        "manifest_version": GROUNDED_SOURCE_MANIFEST_VERSION,
        "sources": sources,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True,
                        help="csf-analyze JSON result; repeat for multiple sources")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG),
                        help="read-only catalog.sqlite path")
    parser.add_argument("--out", required=True,
                        help="manifest JSON output path")
    args = parser.parse_args(argv)
    manifest = build_manifest(args.input, catalog_path=args.catalog)
    out = Path(args.out)
    _write_json_atomic(out, manifest)
    print(json.dumps({"wrote": str(out), "sources": len(manifest["sources"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
