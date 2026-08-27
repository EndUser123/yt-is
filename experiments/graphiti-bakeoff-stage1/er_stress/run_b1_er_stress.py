"""B1 ER-stress driver. NON_DECISION_DIAGNOSTIC. Mechanical: edit nothing.

Delta-review D5 fix: builds the graph through arm_b1.run_b1.build_graphiti so
the extraction/resolution run uses the EXACT pinned Arm B1 stack (proxy LLM
nemotron-3-5-lightning-free @ temperature 0, deterministic fastembed embedder,
local BGE reranker) instead of graphiti's default OpenAI cloud clients.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "fixture_er_stress.json"

STAGE1 = HERE.parent
for p in (str(STAGE1), str(STAGE1 / "arm_b1")):
    if p not in sys.path:
        sys.path.insert(0, p)


def local_normalize(alias: str) -> str:
    """Byte-identical semantics to ef.concept_registry.normalize_alias
    (casefold; hyphen/underscore between word chars -> space; collapse ws;
    strip surrounding punctuation). Replicated so the driver runs without
    importing the yt-is package."""
    s = re.sub(r"(?<=[\w])[-_](?=[\w])", " ", alias.casefold())
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^[^\w]+|[^\w]+$", "", re.sub(r"\s+", " ", s)).strip()


def rows_of(res):
    """Normalize execute_query result shapes into list[dict] (defensive)."""
    if res is None:
        return []
    seq = res if isinstance(res, (list, tuple)) else getattr(res, "records", [res])
    out = []
    for row in seq:
        if isinstance(row, dict):
            out.append(row)
        elif hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
        else:
            out.append({"value": str(row)})
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-number", type=int, required=True)
    ap.add_argument("--llm-mode", choices=[None, "json_object"], default=None)
    args = ap.parse_args()
    n = args.run_number

    for var in ("FALKORDB_HOST", "FALKORDB_PORT", "PROXY_API_KEY"):
        if not os.environ.get(var):
            print(f"MISSING_ENV {var}")
            return 4

    from run_b1 import build_graphiti  # arm_b1 pinned stack

    db = f"er_stress_run{n}"
    if not db.startswith("er_stress_"):
        print("SAFETY: graph name must start with er_stress_")
        return 5
    try:
        g, driver, llm_desc, reranker_desc = await build_graphiti(db, args.llm_mode)
    except Exception as e:  # noqa: BLE001 — fail-fast health check
        print(f"FALKORDB_UNAVAILABLE {os.environ.get('FALKORDB_HOST')}:"
              f"{os.environ.get('FALKORDB_PORT')}: {type(e).__name__}: {e}")
        return 3

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    from graphiti_core.nodes import EpisodeType

    per_episode = []
    for eu in sorted(fixture["evidence_units"], key=lambda u: u["t"]):
        ref_t = datetime.fromisoformat(eu["t"].replace("Z", "+00:00"))
        res = await g.add_episode(
            name=f"{eu['eu_id']} ({eu['source_id']})",
            episode_body=eu["text"],
            source_description=fixture["sources"][
                next(i for i, s in enumerate(fixture["sources"])
                     if s["source_id"] == eu["source_id"])]["channel"],
            source=EpisodeType.message,
            reference_time=ref_t,
            group_id=db,
        )
        per_episode.append({
            "eu_id": eu["eu_id"],
            "t": eu["t"],
            "source_hint": eu["source_id"],
            "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
            "episode_nodes": [
                {"uuid": nd.uuid, "name": nd.name}
                for nd in (getattr(res, "nodes", []) or [])],
            "episode_edges": [
                {"uuid": ed.uuid, "fact": getattr(ed, "fact", None)}
                for ed in (getattr(res, "edges", []) or [])],
        })

    gid = db
    q_nodes = ("MATCH (n:Entity) WHERE n.group_id=$gid RETURN n.uuid AS uuid,"
               " n.name AS name, n.summary AS summary"
               " ORDER BY n.uuid")
    q_edges = ("MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE a.group_id=$gid"
               " RETURN r.uuid AS uuid, r.fact AS fact,"
               " coalesce(r.invalid_at,'') AS invalid_at,"
               " coalesce(r.expired_at,'') AS expired_at,"
               " a.uuid AS source_uuid, b.uuid AS target_uuid ORDER BY r.uuid")
    nodes = rows_of(await driver.execute_query(q_nodes, gid=gid))
    edges = rows_of(await driver.execute_query(q_edges, gid=gid))

    probe_index = []
    for case in fixture["er_cases"]:
        entries = []
        for inp in case["inputs"]:
            rn = local_normalize(inp["ref"])
            matched = []
            for nd in nodes:
                name_n = local_normalize(nd.get("name") or "")
                summ_n = local_normalize(nd.get("summary") or "")
                if rn == name_n or (rn in summ_n and len(rn) >= 3):
                    matched.append(nd.get("uuid"))
            entries.append({
                "ref": inp["ref"], "source_hint": inp.get("source_hint"),
                "context_hint": inp.get("context_hint"),
                "normalized_ref": rn, "matched_node_uuids": matched})
        probe_index.append({"case_id": case["case_id"], "inputs": entries})

    cluster_index = [{"uuid": nd.get("uuid"), "name": nd.get("name"),
                      "summary_excerpt": (nd.get("summary") or "")[:400]}
                     for nd in sorted(nodes, key=lambda d: d.get("uuid") or "")]

    out_path = HERE / f"results_b1_run{n}.json"
    payload = {
        "schema": "er-stress-results-b1-v2",
        "label": "NON_DECISION_DIAGNOSTIC",
        "diagnostic_name": "ENTITY_RESOLUTION_STRESS_DIAGNOSTIC",
        "arm": "B1",
        "run_number": n,
        "falkordb_graph": db,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "fixture_version": fixture["fixture_version"],
        "llm_config": {"llm_client": llm_desc, "reranker": reranker_desc},
        "per_episode_ingest": per_episode,
        "final_nodes_after_resolution": nodes,
        "final_edges_after_resolution": edges,
        "probe_index": probe_index,
        "resolved_clusters": cluster_index,
        "scoring_rule_note": (
            "Apply DIAGNOSTIC.md rubric v1 to probe_index + final_nodes: MERGE_TO_ONE "
            "PASS iff all refs of the case match nodes belonging to ONE canonical "
            "cluster containing the expected entity's canonical surface; DISTINCT "
            "FAIL iff refs whose targets differ map to one node uuid. Wrong-entity "
            "assignment = resolved to a node belonging to another case's cluster."),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"WROTE {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
