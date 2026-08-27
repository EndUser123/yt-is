"""Arm B adapter: Graphiti-core 0.29.3 + FalkorDB, LLM-free.

API findings (verified against installed source under .venv/Lib/site-packages):
- Graphiti.__init__ requires an llm_client/embedder; defaults (OpenAIClient/OpenAIEmbedder)
  need an API key. We pass no-op stubs and never call the LLM pipeline
  (add_episode / resolve_extracted_nodes are NOT used).
- LLM-free ingestion path exists: EntityNode.save(driver), EntityEdge.save(driver),
  EpisodicNode.save(driver), EpisodicEdge.save(driver) write directly via Cypher.
- Bi-temporal fields on EntityEdge: valid_at, invalid_at, expired_at, reference_time.
- Provenance: EntityEdge.episodes (list of episode uuids) and
  EpisodicNode.entity_edges (inverse list). SearchFilters supports
  valid_at/invalid_at/expired_at DateFilters, but the hybrid search path requires
  embeddings, so as-of filtering is done adapter-side on fetched edges.
- Entity dedup/name resolution exists ONLY inside the LLM extraction pipeline
  (resolve_extracted_nodes uses LLM + embedding similarity). LLM-free: none.
  We pre-create canonical EntityNodes with deterministic uuids and store aliases
  in node attributes; alias resolution is adapter code.
- Transactions: FalkorDBDriver does not override driver.transaction(); the base
  class returns a no-op wrapper (queries execute immediately, no rollback).
  No optimistic concurrency anywhere in Graphiti. We implement a generation-token
  CAS layer (this file) because Graphiti provides none.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.edges import EntityEdge, EpisodicEdge
from graphiti_core.graphiti import Graphiti
from graphiti_core.llm_client import LLMClient
from graphiti_core.embedder import EmbedderClient
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode

log = logging.getLogger("arm_b")

GROUP_ID = "stage1"
META_UUID = "stage1-meta-gen"


# ---------------------------------------------------------------- no-op clients
class NoopLLM(LLMClient):
    """Graphiti demands an LLMClient; Arm B ingests LLM-free."""

    async def generate_response(self, messages, **kwargs):  # pragma: no cover
        raise RuntimeError("Arm B runs LLM-free; LLM pipeline must not be reached")


class NoopEmbedder(EmbedderClient):
    async def create(self, input_data):  # pragma: no cover
        raise RuntimeError("Arm B runs embedding-free")


def parse_t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build_graphiti(url: str, graph_name: str = "stage1") -> Graphiti:
    from urllib.parse import urlparse

    u = urlparse(url)
    driver = FalkorDriver(
        host=u.hostname or "127.0.0.1",
        port=u.port or 6379,
        username=u.username,
        password=u.password,
        database=graph_name,
    )
    return Graphiti(graph_driver=driver, llm_client=NoopLLM(), embedder=NoopEmbedder())


# ---------------------------------------------------------------- adapter state
@dataclass
class ClaimGroup:
    subject: str
    predicate: str
    value: str
    eu_ids: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)  # source_id -> eu_id
    supersedes: str | None = None  # value this group's EU supersedes


class ArmB:
    def __init__(self, graphiti: Graphiti):
        self.g = graphiti
        self.driver = graphiti.driver
        self.eu_time: dict[str, datetime] = {}
        self.eu_source: dict[str, str] = {}

    # ------------------------------------------------------------ ingestion
    async def reset(self):
        """Wipe Arm B's graph partition (idempotent fixture load)."""
        await self.driver.execute_query(
            "MATCH (n) WHERE n.group_id = $g DETACH DELETE n", g=GROUP_ID
        )

    async def load_fixture(self, fixture: dict):
        await self.reset()
        nodes: dict[str, EntityNode] = {}
        for e in fixture["entities"]:
            n = EntityNode(
                uuid=f"ent-{e['entity_id']}",
                name=e["canonical_name"],
                group_id=GROUP_ID,
                labels=["Entity"],
                summary="",
                attributes={
                    "entity_id": e["entity_id"],
                    "etype": e["type"],
                    "aliases": json.dumps(e["aliases"]),
                },
            )
            nodes[e["entity_id"]] = n
            await n.save(self.driver)

        meta = EntityNode(
            uuid=META_UUID,
            name="stage1-meta",
            group_id=GROUP_ID,
            summary="",
            attributes={"generation": 0},
        )
        await meta.save(self.driver)

        eus = sorted(fixture["evidence_units"], key=lambda eu: eu["t"])
        for eu in eus:
            await self.ingest_eu(eu, nodes)
        return self

    async def ingest_eu(self, eu: dict, nodes: dict[str, EntityNode]):
        t = parse_t(eu["t"])
        self.eu_time[eu["eu_id"]] = t
        self.eu_source[eu["eu_id"]] = eu["source_id"]
        ep_uuid = f"ep-{eu['eu_id']}"
        edge_uuids: list[str] = []
        mentioned: set[str] = set()

        for i, a in enumerate(eu.get("asserts", [])):
            euuid = f"{eu['eu_id']}#{i}"
            s, p, o = a["subject"], a["predicate"], a["object"]
            mentioned.update({s, o})
            edge = EntityEdge(
                uuid=euuid,
                group_id=GROUP_ID,
                source_node_uuid=nodes[s].uuid,
                target_node_uuid=nodes[o].uuid,
                created_at=t,
                name=p,
                fact=f"{s} {p} {o} (per {eu['eu_id']}, {eu['source_id']})",
                episodes=[ep_uuid],
                valid_at=t,
                reference_time=t,
                attributes={
                    "eu_id": eu["eu_id"],
                    "source_id": eu["source_id"],
                    "value": str(o),
                    "predicate": p,
                    "subject_id": s,
                },
            )
            if "supersedes_value" in a:
                edge.attributes["supersedes_value"] = str(a["supersedes_value"])
            await edge.save(self.driver)
            edge_uuids.append(euuid)

            # Supersession: invalidate the prior-value edge (history stays queryable
            # as-of earlier times). Graphiti's automatic invalidation lives in the
            # LLM pipeline only; here it is adapter code.
            if "supersedes_value" in a:
                await self.driver.execute_query(
                    """
                    MATCH (x:Entity)-[e:RELATES_TO]->(y:Entity)
                    WHERE e.group_id = $g AND e.subject_id = $s AND e.predicate = $p
                      AND e.value = $v AND e.invalid_at IS NULL
                    SET e.invalid_at = $t
                    RETURN e.uuid
                    """,
                    g=GROUP_ID, s=s, p=p, v=str(a["supersedes_value"]), t=t,
                )

        episode = EpisodicNode(
            uuid=ep_uuid,
            name=eu["eu_id"],
            group_id=GROUP_ID,
            source=EpisodeType.text,
            source_description=f"{eu['source_id']} ({eu['eu_id']})",
            content=eu["text"],
            valid_at=t,
            created_at=t,
            entity_edges=edge_uuids,
        )
        await episode.save(self.driver)
        for m in mentioned:
            e = EpisodicEdge(
                group_id=GROUP_ID,
                source_node_uuid=ep_uuid,
                target_node_uuid=nodes[m].uuid,
                created_at=t,
            )
            await e.save(self.driver)

    # ------------------------------------------------------------ reads
    async def all_edges(self) -> list[EntityEdge]:
        records, _, _ = await self.driver.execute_query(
            "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) WHERE e.group_id = $g "
            "RETURN e.uuid AS uuid, e.subject_id AS subject_id, e.predicate AS predicate, "
            "e.value AS value, e.source_id AS source_id, e.eu_id AS eu_id, "
            "e.valid_at AS valid_at, e.invalid_at AS invalid_at, e.expired_at AS expired_at",
            g=GROUP_ID, routing_="r",
        )
        out = []
        for r in records:
            out.append(
                dict(
                    uuid=r["uuid"],
                    subject=r["subject_id"],
                    predicate=r["predicate"],
                    value=r["value"],
                    source=r["source_id"],
                    eu_id=r["eu_id"],
                    valid_at=parse_t(r["valid_at"]) if r["valid_at"] else None,
                    invalid_at=parse_t(r["invalid_at"]) if r["invalid_at"] else None,
                    expired_at=parse_t(r["expired_at"]) if r["expired_at"] else None,
                )
            )
        return out

    @staticmethod
    def live_as_of(e: dict, T: datetime) -> bool:
        """Bi-temporal validity (Graphiti field semantics, adapter-side filter)."""
        if e["valid_at"] is not None and e["valid_at"] > T:
            return False
        for f in ("invalid_at", "expired_at"):
            if e[f] is not None and e[f] <= T:
                return False
        return True

    def claim_groups(self, edges: list[dict]) -> list[ClaimGroup]:
        """Group EUs into claims: same (subject, predicate, value), merging
        supersession chains (EU with supersedes_value links its group to the
        superseded value's group). Contradictions (different values, no
        supersession) stay separate groups -> coexist."""
        by_key: dict[tuple[str, str, str], ClaimGroup] = {}
        for e in edges:
            k = (e["subject"], e["predicate"], e["value"])
            g = by_key.get(k)
            if g is None:
                g = ClaimGroup(*k)
                by_key[k] = g
            g.eu_ids.append(e["eu_id"])
            g.sources.setdefault(e["source"], e["eu_id"])
        return list(by_key.values())

    def apply_supersedes(self, groups: list[ClaimGroup], supersedes_map: dict[str, str]):
        """supersedes_map: eu_id -> superseded value string. Merge the group
        containing (subject, predicate, superseded_value) into the group
        containing the superseding EU (keeping both EU lists)."""
        by_eu: dict[str, ClaimGroup] = {}
        for g in groups:
            for eu in g.eu_ids:
                by_eu[eu] = g
        for eu_id, old_val in supersedes_map.items():
            new_g = by_eu.get(eu_id)
            if new_g is None:
                continue
            for g in groups:
                if g is new_g:
                    continue
                if (g.subject, g.predicate) == (new_g.subject, new_g.predicate) and g.value == old_val:
                    new_g.eu_ids.extend(x for x in g.eu_ids if x not in new_g.eu_ids)
                    new_g.sources.update({k: v for k, v in g.sources.items() if k not in new_g.sources})
                    g.eu_ids = []
                    g.sources = {}

    # ------------------------------------------------------------ query surface
    async def query_claim(self, subject: str, predicate: str, T: datetime | None = None):
        """As-of claim state: live edges grouped into claims with support status.
        Returns list of {value, status, emergence, provenance} for coexisting claims."""
        edges = await self.all_edges()
        if T is not None:
            edges = [e for e in edges if self.live_as_of(e, T)]
        edges = [e for e in edges if e["subject"] == subject and e["predicate"] == predicate]
        groups = [g for g in self.claim_groups(edges) if g.eu_ids]
        self.apply_supersedes(
            groups,
            {e["eu_id"]: s for e, s in self._supersedes.items()},
        )
        groups = [g for g in groups if g.eu_ids]
        out = []
        for g in groups:
            n_sources = len(g.sources)
            # emergence = t of EU from the second independent source
            emergence = None
            if n_sources >= 2:
                times = sorted(self.eu_time[e] for e in g.eu_ids)
                emergence = times[1] if T is None else times[1]
            # current value: from the live edge with the latest valid_at
            live_vals = [e for e in edges if e["eu_id"] in g.eu_ids]
            live_vals.sort(key=lambda e: e["valid_at"])
            out.append(
                dict(
                    value=g.value,
                    current_value=live_vals[-1]["value"] if live_vals else g.value,
                    status="SUPPORTED" if n_sources >= 2 else "ASSERTED-ONLY",
                    emergence=emergence.isoformat() if emergence else None,
                    provenance=sorted(g.eu_ids),
                    sources=sorted(g.sources),
                )
            )
        return out

    async def resolve_alias(self, name: str) -> str | None:
        """Alias -> entity_id. Graphiti's LLM-side entity resolution is unused;
        aliases are stored in EntityNode.attributes and matched adapter-side."""
        records, _, _ = await self.driver.execute_query(
            "MATCH (n:Entity {group_id: $g}) RETURN n.uuid AS uuid, n.name AS name, "
            "n.aliases AS aliases",
            g=GROUP_ID, routing_="r",
        )
        target = name.strip().lower()
        for r in records:
            if r["name"].strip().lower() == target:
                return r["uuid"].replace("ent-", "", 1)
            for a in json.loads(r["aliases"] or "[]"):
                if a.strip().lower() == target:
                    return r["uuid"].replace("ent-", "", 1)
        return None

    async def provenance_of_edge(self, subject: str, predicate: str) -> dict:
        """Episode -> edge provenance (X12). Uses EntityEdge.episodes stored on
        the edge plus the EpisodicNode.entity_edges inverse list."""
        edges = [e for e in await self.all_edges()
                 if e["subject"] == subject and e["predicate"] == predicate]
        return {e["eu_id"] for e in edges}

    async def find_bridge(self, a_id: str, b_id: str) -> list[dict]:
        """2-hop traversal between two entities via an unpredeclared intermediary
        (X6). Pure Cypher over RELATES_TO; Graphiti exposes no fixed-hop
        traversal outside its search/BFS modules, so this is direct Cypher."""
        records, _, _ = await self.driver.execute_query(
            """
            MATCH (a:Entity {uuid: $a})-[e1:RELATES_TO]-(b:Entity)-[e2:RELATES_TO]-(c:Entity {uuid: $b})
            WHERE a <> c AND b.uuid <> $a AND b.uuid <> $c
              AND e1.group_id = $g AND e2.group_id = $g
            RETURN b.uuid AS bridge, b.name AS name,
                   collect(DISTINCT e1.eu_id) + collect(DISTINCT e2.eu_id) AS eus
            """,
            a=f"ent-{a_id}", b=f"ent-{b_id}", g=GROUP_ID, routing_="r",
        )
        out = []
        for r in records:
            eus = sorted({x for x in r["eus"] if x})
            sources = sorted({self.eu_source[e] for e in eus if e in self.eu_source})
            ts = sorted(str(self.eu_time[e]) for e in eus if e in self.eu_time)
            out.append(
                dict(
                    bridge=r["bridge"].replace("ent-", "", 1),
                    bridge_name=r["name"],
                    path=f"{a_id} <- {r['name']} -> {b_id}",
                    supporting_eus=eus,
                    sources=sources,
                    evidence_ts=ts,
                    supported=len(sources) >= 2,
                    novelty="bridge entity was not a predeclared Interest",
                )
            )
        return out

    async def remove_evidence(self, eu_id: str):
        """X7/X8: remove an episode and cascade to its entity edges (edges keep
        other episodes if shared; edges with no remaining episode are deleted).
        Episode node deletion is Graphiti's (DETACH DELETE of MENTIONS);
        the edge-level cascade is adapter code."""
        records, _, _ = await self.driver.execute_query(
            "MATCH (ep:Episodic {uuid: $u}) RETURN ep.entity_edges AS ee", u=f"ep-{eu_id}", routing_="r"
        )
        if not records:
            return
        for euuid in records[0]["ee"] or []:
            await self.driver.execute_query(
                """
                MATCH ()-[e:RELATES_TO {uuid: $u}]->()
                SET e.episodes = [x IN e.episodes WHERE x <> $ep]
                WITH e WHERE size(e.episodes) = 0
                DELETE e
                """,
                u=euuid, ep=f"ep-{eu_id}",
            )
        await self.driver.execute_query(
            "MATCH (ep:Episodic {uuid: $u}) DETACH DELETE ep", u=f"ep-{eu_id}"
        )
        self.eu_time.pop(eu_id, None)
        self.eu_source.pop(eu_id, None)

    async def why_surfaced(self, a_id: str, b_id: str) -> dict | None:
        """X13: explanation bundle for the X6 bridge answer."""
        bridges = await self.find_bridge(a_id, b_id)
        if not bridges:
            return None
        b = bridges[0]
        return dict(
            query=f"bridge {a_id} / {b_id}",
            discovery_route=b["path"],
            supporting_eus=b["supporting_eus"],
            novelty_state=b["novelty"],
            evidence_maturity=dict(
                independent_sources=b["sources"],
                source_count=len(b["sources"]),
                timestamps=b["evidence_ts"],
            ),
            bridge_reason=(
                f"{b['bridge_name']} is asserted to enable both endpoints by "
                f"{len(b['supporting_eus'])} EUs from {len(b['sources'])} independent sources"
            ),
            support_status="SUPPORTED" if b["supported"] else "ASSERTED-ONLY",
        )

    # ------------------------------------------------------------ concurrency (X14)
    async def read_generation(self) -> int:
        records, _, _ = await self.driver.execute_query(
            "MATCH (m:Entity {uuid: $u}) RETURN m.generation AS gen", u=META_UUID, routing_="r"
        )
        return records[0]["gen"] if records else 0

    async def commit_with_generation(self, expected: int, write) -> bool:
        """CAS write against a generation token. Graphiti/FalkorDB provide NO
        transaction or optimistic-concurrency mechanism (FalkorDBDriver inherits
        the no-op transaction() wrapper), so stale-write detection is this
        adapter code. Atomicity rests on Redis single-threaded command
        execution: the guarded SET is one command."""
        records, _, _ = await self.driver.execute_query(
            """
            MATCH (m:Entity {uuid: $u})
            WHERE m.generation = $expected
            SET m.generation = m.generation + 1
            RETURN m.generation AS gen
            """,
            u=META_UUID, expected=expected,
        )
        if not records:
            return False  # stale write rejected
        await write()  # the actual evidence write, after token bump
        return True

    _supersedes: dict[str, str] = {}  # eu_id -> superseded value

    @classmethod
    def register_supersedes(cls, eu_id: str, value: str):
        cls._supersedes[eu_id] = value


async def load(fixture_path: str, url: str) -> ArmB:
    fixture = json.loads(Path(fixture_path).read_text())
    g = build_graphiti(url)
    arm = ArmB(g)
    for eu in fixture["evidence_units"]:
        for a in eu.get("asserts", []):
            if "supersedes_value" in a:
                ArmB.register_supersedes(eu["eu_id"], str(a["supersedes_value"]))
    await arm.load_fixture(fixture)
    return arm
