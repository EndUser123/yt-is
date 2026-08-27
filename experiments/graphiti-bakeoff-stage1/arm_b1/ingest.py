"""Arm B1 ingestion: Graphiti exercised through its REAL semantic pipeline.

Every evidence unit goes through Graphiti.add_episode() with the RAW evidence
text. No add_episode_bulk, no hand-built EntityNode/EntityEdge (that was the B0
diagnostic). Graphiti performs its own entity extraction, resolution/dedup,
relation extraction, edge invalidation/duplicate handling and temporal fields.
Literal-valued objects ("2031", "2M") appear ONLY inside episode text so Graphiti
extracts edges naturally; nothing fabricates literal entity nodes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixture.json"

# Name format carries eu_id/source_id metadata into the graph so evaluators can
# join episode backlinks -> fixture EUs without extra columns.
EPISODE_NAME_RE = re.compile(r"^(?P<eu_id>EU\d+) \((?P<source_id>S\d+)\)$")


def parse_t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass
class IngestRecord:
    eu_id: str
    wall_s: float
    node_count: int = 0
    edge_count: int = 0
    invalidated_count: int = 0
    error: str | None = None


@dataclass
class IngestReport:
    group_id: str
    records: list[IngestReport] = field(default_factory=list)
    total_wall_s: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "episodes_attempted": len(self.records),
            "total_wall_s": round(self.total_wall_s, 3),
            "per_eu": [
                {
                    "eu_id": r.eu_id,
                    "wall_s": round(r.wall_s, 3),
                    "nodes": r.node_count,
                    "edges": r.edge_count,
                    "invalidated": r.invalidated_count,
                    "error": r.error,
                }
                for r in self.records
            ],
        }


def load_fixture(path: Path | None = None) -> dict:
    with open(path or FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def ordered_eus(fixture: dict) -> list[dict]:
    """Evidence units sorted by t ascending (temporal-critical sequential order)."""
    return sorted(fixture["evidence_units"], key=lambda e: parse_t(e["t"]))


async def ingest_fixture(
    graphiti,
    fixture: dict,
    group_id: str,
    run: int,
) -> IngestReport:
    """Sequentially feed each EU through add_episode in t order."""
    from graphiti_core.nodes import EpisodeType

    channels = {s["source_id"]: s["channel"] for s in fixture["sources"]}
    report = IngestReport(group_id=group_id)

    import time as _time

    t0 = _time.perf_counter()
    for eu in ordered_eus(fixture):
        rec = IngestReport(eu_id=eu["eu_id"], wall_s=0.0)
        start = _time.perf_counter()
        try:
            result: Any = await graphiti.add_episode(
                name=f"{eu['eu_id']} ({eu['source_id']})",
                episode_body=eu["text"],
                source_description=f"{channels[eu['source_id']]}",
                reference_time=parse_t(eu["t"]),
                source=EpisodeType.message,
                group_id=group_id,
                uuid=f"{group_id}-{eu['eu_id']}".lower(),
            )
            # AddEpisodeResults merges resolved + invalidated edges into .edges;
            # split them back apart by temporal marker for the report.
            rec.node_count = len(result.nodes)
            rec.edge_count = len(result.edges)
            rec.invalidated_count = sum(1 for e in result.edges if e.invalid_at is not None)
        except Exception as e:  # record and continue: each EU must be attempted sequentially
            rec.error = f"{type(e).__name__}: {e}"
        rec.wall_s = _time.perf_counter() - start
        report.records.append(rec)
    report.total_wall_s = _time.perf_counter() - t0
    return report


def find_episode_uuid(snapshot_episodes: dict[str, dict], eu_id: str) -> str | None:
    for uuid, ep in snapshot_episodes.items():
        m = EPISODE_NAME_RE.match(ep.get("name", ""))
        if m and m.group("eu_id") == eu_id:
            return uuid
    return None
