"""Arm B1 evaluator: answers X1..X14 FROM THE GRAPH against the frozen
preregistration table. agent: zcode

Layout contract:
- SECTION 1 is the backend-neutral SUPPORT RULE / temporal-read module. It is
  THE one place the frozen support model is implemented; other arms reuse this
  section's logic verbatim (conceptual sharing; no cross-arm file edits).
- SECTION 2 adapts live Graphiti/FalkorDB graph state into that neutral model.
  Anything the substrate itself cannot answer falls back to lexical matching on
  stored fields and is flagged ``fallbacks`` per case — never silently.
- SECTION 3 encodes the frozen expected answers and the per-case checks.

Support model note (frozen fixture semantics): claims are keyed by
subject+predicate; supersession compares values within the same key. SUPPORTED
therefore applies at the subject+predicate level (X3's launch_year claim is
supported by EU03 S1 + EU09 S2 even though the asserted value changed), while
VALUE reads answer current/historical questions. Contradiction cases (budget)
keep per-value statuses because they coexist without a supersession marker.

Read-only except X7/X8 (call Graphiti.remove_episode; invalidation/downstream
effects are WRITTEN BY GRAPHITI, not recomputed here) and X14 (two-driver write
simulation). All custom Cypher in this file is read-only MATCH/RETURN except the
single probe+cleanup pair inside X14, which creates and deletes its own labeled
probe node to demonstrate stale-write behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

# =====================================================================
# SECTION 1 — backend-neutral support rule (frozen model, shared logic)
# =====================================================================

THRESHOLD_INDEPENDENT_SOURCES = 2


@dataclass
class AssertionView:
    """One asserting evidence unit behind a claim, backend-neutral."""

    eu_id: str
    source_id: str
    t: datetime


def norm_name(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def support_state(
    assertions: list[AssertionView], threshold: int = THRESHOLD_INDEPENDENT_SOURCES
) -> dict:
    """Frozen support model:
    - SUPPORTED when asserted by EUs from >= threshold distinct source_ids;
    - emergence time = t of the EU from the second independent source;
    - otherwise ASSERTED_ONLY (stored with provenance, not supported)."""
    seen_sources: dict[str, datetime] = {}
    emergence: datetime | None = None
    for a in sorted(assertions, key=lambda x: x.t):
        first_t = seen_sources.get(a.source_id)
        if first_t is None or a.t < first_t:
            seen_sources[a.source_id] = a.t
        if emergence is None and len(seen_sources) >= threshold:
            emergence = a.t
    status = (
        "SUPPORTED"
        if len(seen_sources) >= threshold
        else ("ASSERTED_ONLY" if assertions else "ABSENT")
    )
    return {
        "status": status,
        "distinct_sources": sorted(seen_sources),
        "source_count": len(seen_sources),
        "emergence": _iso(emergence),
        "asserting_eus": sorted({a.eu_id for a in assertions}),
    }


def dt_instant(x: Any) -> datetime | None:
    """Parse stored temporal values (python datetime or ISO string)."""
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.astimezone(timezone.utc) if x.tzinfo else x.replace(tzinfo=timezone.utc)
    s = str(x).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def true_at(valid_at: Any, invalid_at: Any, expired_at: Any, as_of: datetime) -> bool:
    """Bi-temporal point-in-time read: [valid_at, min(invalid_at, expired_at)).

    ``as_of`` is INCLUSIVE at its instant: an edge valid from T answers at T,
    and an edge invalidated AT T has already stopped being true by then."""
    v, inv, exp = dt_instant(valid_at), dt_instant(invalid_at), dt_instant(expired_at)
    if v is not None and v > as_of:
        return False
    if inv is not None and inv <= as_of:
        return False
    if exp is not None and exp <= as_of:
        return False
    return True


# =====================================================================
# SECTION 2 — graph-state reader (Graphiti/FalkorDB -> neutral model)
# =====================================================================

EPISODE_NAME_RE = re.compile(r"^(?P<eu_id>EU\d+) \((?P<source_id>S\d+)\)$")

YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
MILLION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:million\b|\bM\b)", re.IGNORECASE)

# Endpoint-pair keyword disambiguators: facts connecting the same entity pair
# need a predicate hint to be classified (e.g. Alphard<->Helion carries both
# housed_at and partners_with). A matched pair WITHOUT any keyword maps to
# predicate "unclassified" and is excluded from specific-predicate probes.
PREDICATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "researches": ("research", "studi"),
    "housed_at": ("housed",),
    "partners_with": ("partner",),
    "employed_by": ("joined", "employ", "hire"),
    "leads": ("lead", "appoint", "presented"),
    "enables": ("enabl", "critical", "help"),
    "spike_detected": ("spike",),
    "kind": ("outreach", "effort"),
}


@dataclass
class Snapshot:
    group_id: str
    entities: dict[str, dict] = field(default_factory=dict)  # uuid -> {name, summary}
    episodes: dict[str, dict] = field(default_factory=dict)  # uuid -> raw episode record
    edges: list[dict] = field(default_factory=list)
    load_error: str | None = None


async def load_snapshot(driver, group_id: str) -> Snapshot:
    snap = Snapshot(group_id=group_id)
    entity_q = (
        "MATCH (n:Entity {group_id: $g}) "
        "RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary LIMIT 10000"
    )
    episodic_q = (
        "MATCH (e:Episodic {group_id: $g}) "
        "RETURN e.uuid AS uuid, e.name AS name, e.content AS content, "
        "e.source_description AS source_description, e.valid_at AS valid_at, "
        "e.entity_edges AS entity_edges LIMIT 10000"
    )
    edge_q = (
        "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE r.group_id = $g "
        "RETURN r.uuid AS uuid, r.fact AS fact, r.episodes AS episodes, "
        "r.valid_at AS valid_at, r.invalid_at AS invalid_at, r.expired_at AS expired_at, "
        "r.created_at AS created_at, a.uuid AS src_uuid, b.uuid AS tgt_uuid LIMIT 10000"
    )
    for q in (entity_q, episodic_q, edge_q):
        res = await driver.execute_query(q, g=group_id)
        if res is None:
            continue
        records = res[0]
        for r in records:
            rec = dict(r)
            if q is entity_q:
                snap.entities[rec["uuid"]] = {
                    "name": rec.get("name"),
                    "summary": rec.get("summary"),
                }
            elif q is episodic_q:
                snap.episodes[rec["uuid"]] = rec
            else:
                snap.edges.append(rec)
    return snap


def eu_of_episode(ep: dict) -> tuple[str | None, str | None]:
    m = EPISODE_NAME_RE.match(ep.get("name") or "")
    return (m.group("eu_id"), m.group("source_id")) if m else (None, None)


def living_episode_evidence(snap: Snapshot, edge_episodes: Any) -> list[tuple[str, dict]]:
    """Join an edge's episode backlinks to SURVIVING episodic nodes.

    Graphiti's remove_episode deletes whole edges/nodes but does NOT prune the
    removed episode's uuid from surviving edges' episode lists; we therefore
    count only episodes still present in the Episodic store (post-removal
    provenance hygiene is itself part of what X7/X8 measure)."""
    if isinstance(edge_episodes, str):
        try:
            import json as _json

            edge_episodes = _json.loads(edge_episodes)
        except ValueError:
            edge_episodes = []
    return [(u, snap.episodes[u]) for u in (edge_episodes or []) if u in snap.episodes]


def edge_assertions(snap: Snapshot, edge: dict) -> list[AssertionView]:
    avs = []
    for _, ep in living_episode_evidence(snap, edge.get("episodes")):
        eu_id, source_id = eu_of_episode(ep)
        t = dt_instant(ep.get("valid_at"))
        if t is None:
            continue
        avs.append(AssertionView(eu_id=eu_id or "?", source_id=source_id or "?", t=t))
    return avs


def node_ids_for_entity(snap: Snapshot, ents: list[dict], entity_id: str) -> list[str]:
    """Map fixture entity_id onto graph nodes through Graphiti's OWN resolution
    output: the node names it decided to keep after dedup. Normalized-name
    equality against canonical_name; aliases only as containment fallback."""
    ent = next(e for e in ents if e["entity_id"] == entity_id)
    canon = norm_name(ent["canonical_name"])
    exact = [u for u, n in snap.entities.items() if norm_name(n.get("name")) == canon]
    if exact:
        return exact
    wanted = [canon] + [norm_name(a) for a in ent.get("aliases", [])]
    return [u for u, n in snap.entities.items() if norm_name(n.get("name")) in wanted]


def entity_aliases(ent: dict) -> list[str]:
    return [ent["canonical_name"], *ent.get("aliases", [])]


def classify_edge_for_pair(
    edge: dict, src_set: set[str], tgt_set: set[str],
    subject_aliases: list[str], object_aliases: list[str],
) -> tuple[bool, str | None]:
    """Does this edge represent a subject--object relation? Primary signal:
    Graphiti endpoint resolution (src/tgt node uuids); secondary: alias tokens
    in fact text. Returns (matched, predicate_kind|None|'unclassified')."""
    su, tu = edge.get("src_uuid"), edge.get("tgt_uuid")
    endpoint_match = (su in src_set and tu in tgt_set) or (su in tgt_set and tu in src_set)
    fact_l = norm_name(edge.get("fact") or "")
    alias_match = any(norm_name(a) in fact_l for a in subject_aliases) and any(
        norm_name(a) in fact_l for a in object_aliases
    )
    if not (endpoint_match or alias_match):
        return False, None
    kind = "unclassified"
    for pred, kws in PREDICATE_KEYWORDS.items():
        if any(k in fact_l for k in kws):
            kind = pred
            break
    return True, kind


def find_relation_edges(
    snap: Snapshot, ents: list[dict], subject_id: str, object_id: str
) -> list[dict]:
    src = set(node_ids_for_entity(snap, ents, subject_id))
    tgt = set(node_ids_for_entity(snap, ents, object_id))
    subj_a = entity_aliases(next(e for e in ents if e["entity_id"] == subject_id))
    obj_a = entity_aliases(next(e for e in ents if e["entity_id"] == object_id))
    out = []
    for e in snap.edges:
        ok, kind = classify_edge_for_pair(e, src, tgt, subj_a, obj_a)
        if ok:
            out.append({**e, "_predicate": kind})
    return out


def find_literal_edges(
    snap: Snapshot, ents: list[dict], subject_id: str, literal_rx: re.Pattern
) -> list[dict]:
    """Literal-valued assertions (launch_year, budget): the literal lives ONLY
    inside fact text (per ingest contract Graphiti extracted it naturally; we
    never fabricated literal nodes). Selects edges incident to resolved subject
    nodes whose fact carries the literal token."""
    ids = set(node_ids_for_entity(snap, ents, subject_id))
    out = []
    for e in snap.edges:
        if e.get("src_uuid") in ids or e.get("tgt_uuid") in ids:
            m = literal_rx.search(e.get("fact") or "")
            if m:
                out.append({**e, "_literal": m.group(1)})
    return out


def normalize_money(v: str) -> str:
    f = float(v)
    return f"{int(f)}M" if f.is_integer() else f"{f}M"


NOW_SENTINEL = datetime(9999, 12, 31, tzinfo=timezone.utc)


def value_reads(snap: Snapshot, candidates: list[dict], as_of: datetime | None) -> dict:
    """Per-value temporal read of candidate edges using ONLY graphiti-written
    invalidation fields. Evidence/status computed per value bucket.

    Reads filter BOTH levels: an edge alive at T contributes only those of its
    asserting episodes with t <= T (a later corroboration of a long-lived edge
    must not leak backward). ``as_of=None`` reads NOW: superseded values are
    dead by construction."""
    effective = as_of if as_of is not None else NOW_SENTINEL
    buckets: dict[str, list[AssertionView]] = {}
    dropped_before_asof: list[str] = []
    for e in candidates:
        val = e.get("_literal")
        if not true_at(e.get("valid_at"), e.get("invalid_at"), e.get("expired_at"),
                       effective):
            if val:
                dropped_before_asof.append(val)
            continue
        if val:
            avs = [a for a in edge_assertions(snap, e) if a.t <= effective]
            buckets.setdefault(val, []).extend(avs)
    values = {}
    for val, avs in buckets.items():
        st = support_state(avs)
        st["evidence"] = [
            {"eu_id": a.eu_id, "source_id": a.source_id, "t": _iso(a.t)} for a in avs
        ]
        values[val] = st
    return {"values": values, "not_true_at_asof": sorted(set(dropped_before_asof))}


def predicate_status_at(
    snap: Snapshot, candidates: list[dict], as_of: datetime | None,
    *, include_superseded: bool = False,
) -> tuple[dict, list[AssertionView], list[dict]]:
    """Subject+predicate level support/read state (frozen claim key).

    Two intent modes:
    - include_superseded=False (answer-at-a-time): an edge must be alive at
      as_of and only assertions with t <= as_of count (temporal read).
    - include_superseded=True (evidential history of the CLAIM): invalidated
      edges still contributed real evidence to the subject+predicate key while
      they were live, so they count toward SUPPORTED; per-EU temporal filter
      applies whenever as_of is given."""
    effective = as_of if as_of is not None else NOW_SENTINEL
    used: list[AssertionView] = []
    per_edge = []
    for e in candidates:
        if not include_superseded and not true_at(
            e.get("valid_at"), e.get("invalid_at"), e.get("expired_at"), effective
        ):
            continue
        avs_all = edge_assertions(snap, e)
        avs = [a for a in avs_all if a.t <= effective]
        used.extend(avs)
        per_edge.append(
            {
                "edge_uuid": e["uuid"],
                "fact": e.get("fact"),
                "predicate_class": e.get("_predicate"),
                "valid_at": _iso(dt_instant(e.get("valid_at"))),
                "invalid_at": _iso(dt_instant(e.get("invalid_at"))),
                "expired_at": _iso(dt_instant(e.get("expired_at"))),
                "assertions": [
                    {"eu_id": a.eu_id, "source_id": a.source_id, "t": _iso(a.t)}
                    for a in avs_all
                ],
                "assertions_excluded_by_asof": [
                    {"eu_id": a.eu_id, "t": _iso(a.t)}
                    for a in avs_all
                    if a.t > effective
                ],
            }
        )
    return support_state(used), used, per_edge


async def bridge_paths_cypher(driver, group_id: str, uuid_a: str, uuid_b: str) -> list[dict]:
    """READ-ONLY bridge discovery via Cypher traversal (custom semantic LOC):
    every common neighbor linking A and B through RELATES_TO edges."""
    q = (
        "MATCH (a:Entity {group_id: $g})-[r1:RELATES_TO]-(m:Entity {group_id: $g})"
        "-[r2:RELATES_TO]-(c:Entity {group_id: $g}) "
        "WHERE a.uuid = $ua AND c.uuid = $ub AND m.uuid <> $ua AND m.uuid <> $ub "
        "AND id(r1) <> id(r2) AND id(a) <> id(c) "
        "RETURN DISTINCT m.uuid AS bridge_uuid, m.name AS bridge_name, "
        "collect(DISTINCT r1.uuid) AS left_edge_uuids, "
        "collect(DISTINCT r2.uuid) AS right_edge_uuids LIMIT 50"
    )
    res = await driver.execute_query(q, g=group_id, ua=uuid_a, ub=uuid_b)
    return [dict(r) for r in (res[0] if res else [])]


def hop_source_sets(
    snap: Snapshot, edge_map: dict[str, dict], path: dict
) -> tuple[set[str], set[str]]:
    """Sources behind each hop of a candidate bridge path (via episode backlinks)."""
    out: dict[str, set[str]] = {}
    for side in ("left_edge_uuids", "right_edge_uuids"):
        srcs: set[str] = set()
        for uid in path.get(side) or []:
            e = edge_map.get(uid)
            if e is None:
                continue
            for a in edge_assertions(snap, e):
                if a.source_id:
                    srcs.add(a.source_id)
        out[side] = srcs
    return out["left_edge_uuids"], out["right_edge_uuids"]


def admits_bridge(
    snap: Snapshot, edge_map: dict[str, dict], path: dict
) -> tuple[bool, dict]:
    """Delta-review D3: Arm-A-equivalent `adds_source` admission rule, shared
    semantics with arm_a/store.py find_bridges: the far hop must contribute an
    independent source NOT already present on the near side AND the aggregate
    must meet the frozen SUPPORTED bar; a node merely related to both topics on
    one strong side is not surfaced as the bridge."""
    left, right = hop_source_sets(snap, edge_map, path)
    combined = [
        a
        for side in ("left_edge_uuids", "right_edge_uuids")
        for uid in path.get(side) or []
        if (e := edge_map.get(uid)) is not None
        for a in edge_assertions(snap, e)
    ]
    agg = support_state(combined)
    adds = len(agg["distinct_sources"]) > max(len(left), len(right))
    ok = bool(agg["status"] == "SUPPORTED" and adds)
    detail = {
        "src_left": sorted(left),
        "src_right": sorted(right),
        "aggregate_sources": agg["distinct_sources"],
        "adds_source": bool(adds),
        "aggregate_status": agg["status"],
    }
    return ok, detail


def select_bridge_path(
    snap: Snapshot, paths: list[dict], fixture_entities: list[dict]
) -> tuple[dict | None, str]:
    """Pick THE bridging path the fixture means: prefer a bridge node resolved
    as B1 (cryogenic cooling); otherwise fall back to any two-hop path and say
    so honestly in the returned reason (alternative routes exist)."""
    if not paths:
        return None, "no two-hop path"
    try:
        b1 = set(node_ids_for_entity(snap, fixture_entities, "B1"))
    except StopIteration:
        b1 = set()
    cryo = [p for p in paths if p.get("bridge_uuid") in b1]
    if cryo:
        return cryo[0], "resolved to B1 node"
    token = [p for p in paths if "cryo" in norm_name(p.get("bridge_name"))]
    if token:
        return token[0], "bridge identified by cryo- name token"
    return paths[0], f"NO B1 path; first of {len(paths)} alternative route(s)"


# =====================================================================
# SECTION 3 — frozen expectations + cases X1..X14
# =====================================================================

EXPECTED: dict[str, dict] = {
    "X1": {"id": "X1 as-of 2026-01-15",
           "expected": {"value": "2031", "claim_status": "ASSERTED_ONLY"}},
    "X2": {"id": "X2 as-of 2026-01-19",
           "expected": {"claim_status": "SUPPORTED", "emergence": "2026-01-18T00:00:00+00:00"}},
    "X3": {"id": "X3 now launch_year current",
           "expected": {"current_value": "2033", "claim_status_predicate_level": "SUPPORTED",
                        "historical_as_of_2026-01-19": "2031"}},
    "X4": {"id": "X4 now budget",
           "expected": {"coexisting_values": ["2M", "5M"], "each_status": "ASSERTED_ONLY"}},
    "X5": {"id": "X5 alias identity",
           "expected": {"E1_surfaces": ["the alphard program", "alphard initiative", "alphard"],
                        "E3_distinct_from_E1": True}},
    "X6": {"id": "X6 bridge",
           "expected": {"bridge_via": "B1", "claim_status": "SUPPORTED",
                        "emergence": "2026-01-24T00:00:00+00:00"}},
    "X7": {"id": "X7 remove EU08",
           "expected": {"partners_after": "ASSERTED_ONLY", "researches_T1": "SUPPORTED"}},
    "X8": {"id": "X8 remove EU11",
           "expected": {"bridge": "GONE", "enables_T1": "PRESENT"}},
    "X9": {"id": "X9 now leads",
           "expected": {"claim_status": "SUPPORTED", "emergence": "2026-02-02T00:00:00+00:00"}},
    "X10": {"id": "X10 as-of 2026-01-26 inclusive",
            "expected": {"claim_status": "ASSERTED_ONLY", "post_T_leak": False}},
    "X11": {"id": "X11 replay checkpoints",
            "expected": {"2026-01-05": "researches T1 SUPPORTED",
                         "2026-01-18": "partners emerges SUPPORTED",
                         "2026-01-20": "launch 2031->2033",
                         "2026-02-02": "leads SUPPORTED",
                         "no_post_T_leak_each_checkpoint": True}},
    "X12": {"id": "X12 provenance",
            "expected": {"partners_with_E1_O1": ["EU06", "EU08"],
                         "researches_E1_T1": ["EU01", "EU02"]}},
    "X13": {"id": "X13 why-surfaced",
            "expected": {"required_fields": ["route", "evidence", "novelty_state",
                                             "maturity", "reason"]}},
    "X14": {"id": "X14 concurrency/stale-write",
            "expected": {"behavior_recorded_not_assumed": True}},
}


def case_result(cid: str, actual: Any, *, ok: bool | None, failure_class: str | None = None,
                fallbacks: list[str] | None = None, notes: str | None = None) -> dict:
    status = "UNTESTABLE" if ok is None else ("PASS" if ok else "FAIL")
    return {
        "id": EXPECTED[cid]["id"],
        "case": cid,
        "expected": EXPECTED[cid]["expected"],
        "actual": actual,
        "status": status,
        "failure_class": failure_class,
        "fallback_flags": fallbacks or [],
        "notes": notes,
    }


FALLBACK_LITERAL = "literal scan on stored fact text (Graphiti embeds literals in facts)"
FALLBACK_NAMEJOIN = "normalized-name join over Graphiti-resolved node names"


class B1Evaluator:
    def __init__(self, graphiti, driver, fixture: dict, group_id: str,
                 second_session_add: Callable[..., Any] | None = None):
        self.graphiti = graphiti
        self.driver = driver
        self.fixture = fixture
        self.group_id = group_id
        self.entities = fixture["entities"]
        # run_b1 injects this so Session B can add new evidence through the FULL
        # Graphiti pipeline over its own driver connection (X14).
        self.second_session_add = second_session_add

    async def fresh_snapshot(self) -> Snapshot:
        return await load_snapshot(self.driver, self.group_id)

    def t(self, iso: str) -> datetime:
        d = dt_instant(iso)
        assert d is not None
        return d

    # ------------------------------------------------------------------- cases
    async def x1_launch_asof(self, snap: Snapshot) -> dict:
        cands = [e for e in find_literal_edges(snap, self.entities, "E2", YEAR_RE)]
        view = value_reads(snap, cands, as_of=self.t("2026-01-15T00:00:00Z"))
        got = view["values"].get("2031")
        ok = bool(got) and got["status"] == "ASSERTED_ONLY" and got["distinct_sources"] == ["S1"]
        return case_result("X1", {"read_as_of_2026-01-15": view}, ok=ok,
                           failure_class=None if ok else ("F-prov" if got else "F-supersede"),
                           fallbacks=[FALLBACK_LITERAL])

    async def x2_partners_asof(self, snap: Snapshot) -> dict:
        cands = [e for e in find_relation_edges(snap, self.entities, "E1", "O1")
                 if e.get("_predicate") == "partners_with"]
        st, used, per_edge = predicate_status_at(
            snap, cands, as_of=self.t("2026-01-19T00:00:00Z"))
        ok = st["status"] == "SUPPORTED" and st["emergence"] == "2026-01-18T00:00:00+00:00"
        return case_result("X2", {"claim": st, "edges": per_edge}, ok=ok,
                           failure_class=None if ok else "F-prov")

    async def x3_launch_current(self, snap: Snapshot) -> dict:
        cands = find_literal_edges(snap, self.entities, "E2", YEAR_RE)
        now_view = value_reads(snap, cands, as_of=None)
        hist_view = value_reads(snap, cands, as_of=self.t("2026-01-19T00:00:00Z"))
        # predicate-level SUPPORTED counts the claim's evidential history
        # (EU03 S1 + EU09 S2 across the superseded and current values)
        pred_st, _, _ = predicate_status_at(
            snap, cands, as_of=None, include_superseded=True)
        cur_2033 = now_view["values"].get("2033")
        hist_2031 = hist_view["values"].get("2031")
        superseded_marker = any(e.get("invalid_at") is not None
                                for e in cands if e.get("_literal") == "2031")
        ok = (bool(cur_2033) and bool(hist_2031) and superseded_marker
              and pred_st["status"] == "SUPPORTED")
        fcls = None
        if not ok:
            fcls = ("F-supersede" if (cur_2033 and hist_2031)
                    else ("F-collapse" if not cur_2033 else "F-prov"))
        return case_result(
            "X3",
            {"current_value_read": now_view, "historical_2026-01-19": hist_view,
             "predicate_level_claim": pred_st,
             "supersession_marker_invalid_at_written_by_graphiti": superseded_marker},
            ok=ok, failure_class=fcls, fallbacks=[FALLBACK_LITERAL])

    async def x4_budget_coexist(self, snap: Snapshot) -> dict:
        cands = []
        for e in find_literal_edges(snap, self.entities, "E1", MILLION_RE):
            e["_literal"] = normalize_money(e["_literal"])
            cands.append(e)
        view = value_reads(snap, cands, as_of=None)
        vals = view["values"]
        coexists = {"2M", "5M"} <= set(vals)
        each_single = all(vals[v]["status"] == "ASSERTED_ONLY"
                          for v in ("2M", "5M") if v in vals)
        neither_invalidated = all(e.get("invalid_at") is None for e in cands)
        ok = coexists and each_single and neither_invalidated
        return case_result(
            "X4", {"current_value_read": view,
                    "no_supersession_marker_between_values": neither_invalidated},
            ok=ok,
            failure_class=None if ok else ("F-collapse" if not coexists else "F-prov"),
            fallbacks=[FALLBACK_LITERAL])

    async def x5_identity(self, snap: Snapshot) -> dict:
        """Alias identity through Graphiti's resolution OUTPUT: distinct nodes
        per fixture entity plus per-surface co-reference over stored facts.
        Co-reference = some fact mentions the surface as a whole token AND has
        an endpoint on that entity's resolved node."""
        nodes_seen = [{"uuid": u, "name": n.get("name")}
                      for u, n in sorted(snap.entities.items())]
        e1 = set(node_ids_for_entity(snap, self.entities, "E1"))
        e3 = set(node_ids_for_entity(snap, self.entities, "E3"))

        def whole_token_in_fact(fact: str, surf: str) -> bool:
            return re.search(rf"(?<![a-z0-9]){re.escape(norm_name(surf))}(?![a-z0-9])",
                             norm_name(fact)) is not None

        def co_refers(surf: str, ent_nodes: set[str]) -> bool:
            return any(whole_token_in_fact(e.get("fact") or "", surf)
                       and ({e.get("src_uuid"), e.get("tgt_uuid")} & ent_nodes)
                       for e in snap.edges)

        def resolved_names(surface: str, ent_nodes: set[str]) -> list[str]:
            """Names Graphiti produced that this surface denotes: exact resolved
            node-name match, else names of the entity's own nodes containing it."""
            s = norm_name(surface)
            direct = [n.get("name") for n in snap.entities.values()
                      if norm_name(n.get("name")) == s]
            if direct:
                return [x for x in direct if x]
            return [snap.entities[u].get("name") for u in sorted(ent_nodes)
                    if u in snap.entities
                    and s in norm_name(snap.entities[u].get("name"))]

        checks = {
            al: co_refers(al, e1)
            for al in ("the Alphard program", "ALPHARD initiative", "Alphard")
        }
        report = {
            "graphiti_node_inventory": nodes_seen,
            "surface_resolutions": {al: resolved_names(al, e1)
                                     for al in ("the Alphard program", "ALPHARD initiative",
                                                "Alphard")},
            "surface_resolutions_minor": {"Alphard Minor": resolved_names("Alphard Minor", e3)},
            "per_surface_checks": checks,
            "E1_nodes": sorted(e1),
            "E3_nodes": sorted(e3),
            "merged_collision_E1_E3": bool(e1 & e3),
            "minor_resolves_to_E1": co_refers("Alphard Minor", e1),
        }
        all_surfaces_ok = all(checks.values())
        distinct_ok = bool(e3) and not (e1 & e3)
        minor_ok = bool(report["E3_nodes"]) and not report["minor_resolves_to_E1"]
        ok = all_surfaces_ok and distinct_ok and minor_ok
        return case_result("X5", report, ok=bool(ok),
                           failure_class=None if ok else "F-identity",
                           fallbacks=[FALLBACK_NAMEJOIN,
                                      "alias co-reference checked over stored fact text"])

    async def x6_bridge(self, snap: Snapshot) -> dict:
        t1 = node_ids_for_entity(snap, self.entities, "T1")
        t2 = node_ids_for_entity(snap, self.entities, "T2")
        if len(t1) != 1 or len(t2) != 1:
            return case_result("X6", {"T1_nodes": t1, "T2_nodes": t2}, ok=False,
                               failure_class="F-identity",
                               notes="endpoint nodes did not resolve uniquely")
        paths = await bridge_paths_cypher(self.driver, self.group_id, t1[0], t2[0])
        edge_map_pre = {e["uuid"]: e for e in snap.edges}
        admissions = {}
        admitted: list[dict] = []
        for pth in paths:
            ok_adm, detail = admits_bridge(snap, edge_map_pre, pth)
            admissions[pth.get("bridge_uuid")] = detail
            if ok_adm:
                admitted.append(pth)
        path, sel_reason = select_bridge_path(snap, admitted, self.entities)
        if path is None:
            return case_result(
                "X6",
                {"paths_found": len(paths), "admitted": 0,
                 "admission_detail": list(admissions.values())},
                ok=False,
                failure_class="F-bridge" if paths else "F-bridge",
                notes=("no two-hop path T1-*-T2 exists in stored edges"
                       if not paths else
                       "paths exist but none passed the adds_source admission rule"))
        edge_map = {e["uuid"]: e for e in snap.edges}
        hops = {}
        combined: list[AssertionView] = []
        for side in ("left_edge_uuids", "right_edge_uuids"):
            hop_rows = []
            for uid in path.get(side) or []:
                e = edge_map.get(uid)
                if e is None:
                    continue
                avs = edge_assertions(snap, e)
                hop_rows.append({
                    "edge_uuid": uid, "fact": e.get("fact"),
                    "valid_at": _iso(dt_instant(e.get("valid_at"))),
                    "invalid_at": _iso(dt_instant(e.get("invalid_at"))),
                    "evidence": [{"eu_id": a.eu_id, "source_id": a.source_id,
                                  "t": _iso(a.t)} for a in avs],
                })
                combined.extend(avs)
            hops[side] = hop_rows
        both_hops_alive = all(hops[s] for s in hops)
        agg = support_state(combined) if both_hops_alive else support_state([])
        novelty = ("discovered-not-seeded: B1 entered the graph solely through its enabling "
                   "statements; the fixture predeclares no Interest seed list for Arm B, so "
                   "'was not a predeclared Interest' holds structurally under this ingest")
        ok = (both_hops_alive and agg["status"] == "SUPPORTED"
              and agg["emergence"] == "2026-01-24T00:00:00+00:00")
        return case_result(
            "X6",
            {"route": {"type": "path", "selection": sel_reason,
                       "via_bridge_node":
                       {"uuid": path.get("bridge_uuid"), "name": path.get("bridge_name")},
                       "hop_edges": hops,
                       "admission": admissions.get(path.get("bridge_uuid"))},
             "aggregate_claim": agg, "novelty_note": novelty},
            ok=bool(ok), failure_class=None if ok else "F-bridge",
            fallbacks=["cryogenic bridge identified by name token among Cypher result rows"])

    async def x9_leads_now(self, snap: Snapshot) -> dict:
        cands = find_relation_edges(snap, self.entities, "P1", "E1")
        st, used, per_edge = predicate_status_at(snap, cands, as_of=None)
        emerg_ok = st["emergence"] == "2026-02-02T00:00:00+00:00"
        ok = st["status"] == "SUPPORTED" and emerg_ok
        return case_result(
            "X9", {"claim": st, "edges": per_edge}, ok=ok,
            failure_class=None if ok else "F-prov",
            fallbacks=["P1->E1 predicate class accepts appointment/presentation phrasing "
                       "(fixture EU15 asserts leads via a presentation)"])

    async def x10_leads_asof_inclusive(self, snap: Snapshot) -> dict:
        cut = self.t("2026-01-26T00:00:00Z")
        cands = find_relation_edges(snap, self.entities, "P1", "E1")
        st, used, per_edge = predicate_status_at(snap, cands, as_of=cut)
        # Explicit leak measurement on the ASSEMBLED answer: any included
        # assertion after T (must be zero by construction; checked anyway).
        leaked = any(a.t > cut for a in used)
        excluded = [
            ex for e in per_edge for ex in e.get("assertions_excluded_by_asof", [])
        ]
        ok = (st["status"] == "ASSERTED_ONLY" and st["distinct_sources"] == ["S2"]
              and not leaked)
        return case_result(
            "X10",
            {"claim_as_of_T_inclusive": st,
             "post_T_leak": bool(leaked),
             "excluded_post_T_assertions": excluded,
             "boundary_rule": "edges/assertions with valid_at == T included; EU15 at "
                              "T+7d excluded from both edge-alive and evidence filters"},
            ok=ok, failure_class=None if ok else "F-leak")

    async def x11_replay_checkpoints(self, snap: Snapshot) -> dict:
        researches = [e for e in find_relation_edges(snap, self.entities, "E1", "T1")
                      if e.get("_predicate") == "researches"]
        partners = [e for e in find_relation_edges(snap, self.entities, "E1", "O1")
                    if e.get("_predicate") == "partners_with"]
        launch = find_literal_edges(snap, self.entities, "E2", YEAR_RE)
        leads = find_relation_edges(snap, self.entities, "P1", "E1")

        def read_pred(cands, cp):
            st, used, _ = predicate_status_at(snap, cands, as_of=cp)
            leak = any(a.t > cp for a in used)
            return st, used, leak

        results = {}
        ok_all = True

        cps = [
            ("2026-01-05T00:00:00Z", "researches_T1_SUPPORTED",
             researches, lambda st: st["status"] == "SUPPORTED"),
            ("2026-01-18T00:00:00Z", "partners_SUPPORTED",
             partners, lambda st: st["status"] == "SUPPORTED"),
            ("2026-01-20T00:00:00Z", "launch_current_2033",
             launch, "LITERAL2033"),
            ("2026-02-02T00:00:00Z", "leads_SUPPORTED",
             leads, lambda st: st["status"] == "SUPPORTED"),
        ]
        for cp_iso, kind, cands, check in cps:
            cp = self.t(cp_iso)
            if check == "LITERAL2033":
                view = value_reads(snap, cands, as_of=cp)
                v33 = view["values"].get("2033")
                v31 = view["values"].get("2031") or {}
                ev_ts = [
                    dt_instant(ev.get("t"))
                    for val in view["values"].values()
                    for ev in (val.get("evidence") or [])
                ]
                leak = any(ts is not None and ts > cp for ts in ev_ts)
                # Delta-review D4: the frozen transition "2031 -> 2033" requires
                # the superseded value NOT to remain a live candidate here
                # (Arm A enforces exactly ["2033"] at this checkpoint).
                old_gone = not v31 or not v31.get("evidence")
                passed = (bool(v33) and v33["status"] in ("SUPPORTED", "ASSERTED_ONLY")
                          and old_gone)
                state_out = {**view,
                             "superseded_2031_still_live": bool(not old_gone)}
            else:
                st, used, leak = read_pred(cands, cp)
                passed = check(st) and not leak
                state_out = st
            results[cp_iso] = {
                "checkpoint": kind, "passed": bool(passed and not leak),
                "state": state_out, "leak_free": not leak,
            }
            ok_all = ok_all and results[cp_iso]["passed"]

        again = await self.fresh_snapshot()
        stable = (len(again.edges) == len(snap.edges)
                  and len(again.episodes) == len(snap.episodes))
        return case_result("X11", {"checkpoints": results,
                                    "snapshot_stable_between_reads": stable},
                           ok=bool(ok_all and stable),
                           failure_class=None if ok_all and stable else "F-replay")

    async def x12_provenance(self, snap: Snapshot) -> dict:
        def prov(cands) -> list[str]:
            eus: set[str] = set()
            for e in cands:
                for _, ep in living_episode_evidence(snap, e.get("episodes")):
                    eu, _ = eu_of_episode(ep)
                    if eu:
                        eus.add(eu)
            return sorted(eus)

        parts = [e for e in find_relation_edges(snap, self.entities, "E1", "O1")
                 if e.get("_predicate") == "partners_with"]
        res = [e for e in find_relation_edges(snap, self.entities, "E1", "T1")
               if e.get("_predicate") == "researches"]
        p, r = prov(parts), prov(res)
        ok = p == ["EU06", "EU08"] and r == ["EU01", "EU02"]
        return case_result(
            "X12",
            {"partners_with_E1_O1": p, "researches_E1_T1": r,
             "mechanism": "EntityEdge.episodes backlinks joined to surviving Episodic store"},
            ok=ok, failure_class=None if ok else "F-prov")

    # ------------------------------------------------------- removal cases ----
    def _episode_for_eu(self, snap: Snapshot, eu_id: str) -> str | None:
        for uid, ep in snap.episodes.items():
            eu, _ = eu_of_episode(ep)
            if eu == eu_id:
                return uid
        return None

    async def x7_remove_eu08(self, snap_pre: Snapshot) -> dict:
        target = self._episode_for_eu(snap_pre, "EU08")
        if target is None:
            return case_result("X7", {"error": "episode EU08 not found"}, ok=False,
                               failure_class="F-removal")
        await self.graphiti.remove_episode(target)
        post = await self.fresh_snapshot()

        partners = [e for e in find_relation_edges(post, self.entities, "E1", "O1")
                    if e.get("_predicate") == "partners_with"]
        st_p = support_state([a for e in partners for a in edge_assertions(post, e)])
        researches = [e for e in find_relation_edges(post, self.entities, "E1", "T1")
                      if e.get("_predicate") == "researches"]
        st_r = support_state([a for e in researches for a in edge_assertions(post, e)])
        others_intact = len(post.episodes) == len(snap_pre.episodes) - 1
        ok = (st_p["status"] == "ASSERTED_ONLY" and st_r["status"] == "SUPPORTED"
              and others_intact)
        return case_result(
            "X7",
            {"partners_with_after_removal": st_p, "researches_after_removal": st_r,
             "other_evidence_untouched": others_intact,
             "removal_mechanism": "graphiti.remove_episode: deletes edges whose FIRST "
                                  "backlink is the removed episode and nodes mentioned "
                                  "only by it; downstream downgrades here are MEASURED, "
                                  "not recomputed evaluator-side"},
            ok=ok, failure_class=None if ok else "F-removal")

    async def x8_remove_eu11(self) -> dict:
        snap_pre = await self.fresh_snapshot()
        target = self._episode_for_eu(snap_pre, "EU11")
        if target is None:
            return case_result("X8", {"error": "episode EU11 not found"}, ok=False,
                               failure_class="F-removal")
        await self.graphiti.remove_episode(target)
        post = await self.fresh_snapshot()
        t1 = node_ids_for_entity(post, self.entities, "T1")
        t2 = node_ids_for_entity(post, self.entities, "T2")
        b1_paths: list[dict] = []
        any_paths: list[dict] = []
        if len(t1) == 1 and len(t2) == 1:
            all_paths = await bridge_paths_cypher(self.driver, self.group_id,
                                                  t1[0], t2[0])
            chosen, sel_reason = select_bridge_path(post, all_paths, self.entities)
            # The FROZEN claim is the B1 bridge specifically; alternative routes
            # through other common neighbors do not resurrect it.
            if sel_reason.startswith("resolved") or sel_reason.startswith("bridge identified"):
                b1_paths = [chosen]
            any_paths = all_paths
        bridge_gone = not b1_paths
        enables = [e for e in find_relation_edges(post, self.entities, "B1", "T1")
                   if e.get("_predicate") == "enables"]
        enables_alive = bool(enables) and all(
            true_at(e.get("valid_at"), e.get("invalid_at"), e.get("expired_at"),
                    self.t("2999-12-31T00:00:00Z")) for e in enables)
        b1_present = bool(node_ids_for_entity(post, self.entities, "B1"))
        ok = bridge_gone and enables_alive and b1_present
        return case_result(
            "X8",
            {"b1_bridge_present_after_removal": not bridge_gone,
             "alternative_routes_remaining": len(any_paths),
             "enables_B1_T1_alive": enables_alive,
             "B1_node_still_present": b1_present},
            ok=ok, failure_class=None if ok else "F-removal")

    async def x13_why_surfaced(self, x6_result: dict) -> dict:
        actual = x6_result.get("actual") or {}
        route = actual.get("route") or {}
        agg = actual.get("aggregate_claim") or {}
        hop_rows = [r for side in ("left_edge_uuids", "right_edge_uuids")
                    for r in (route.get("hop_edges", {}) or {}).get(side, [])]
        evidence = [ev for row in hop_rows for ev in (row.get("evidence") or [])]
        timestamps = sorted({ev["t"] for ev in evidence if ev.get("t")})
        bundle = {
            "route": {"type": "path",
                       "discovered_by": "read-only Cypher 2-hop traversal in FalkorDB",
                       "path": route},
            "evidence": hop_rows,
            "novelty_state": "discovered-not-seeded" if evidence else "none",
            "maturity": {"source_count": agg.get("source_count"),
                          "sources": agg.get("distinct_sources"),
                          "timestamps": timestamps},
            "reason": ("two independent sources asserted the two half-links of the bridge "
                       "within the window; aggregation reaches the frozen support "
                       f"threshold of {THRESHOLD_INDEPENDENT_SOURCES}"
                       ) if evidence else "",
        }
        required = EXPECTED["X13"]["expected"]["required_fields"]
        complete = all(bundle[f] for f in required)
        consistent = bool(route) and agg.get("status") == "SUPPORTED"
        ok = bool(complete and consistent)
        return case_result("X13",
                           {"bundle": bundle, "complete": complete,
                            "consistent_with_x6": consistent},
                           ok=ok, failure_class=None if ok else "F-bridge")

    async def x14_concurrency(self) -> dict:
        """Two sessions write; behavior OBSERVED, not assumed.

        Static ground truth (verified in installed graphiti-core 0.29.3 source):
        - graphiti_core/driver/falkordb_driver.py does NOT override transaction();
          base GraphDriver.transaction() yields a no-op session wrapper executing
          each write immediately (driver/driver.py). No optimistic CAS, no
          generation tokens, no multi-statement transactions.
        The runtime probe simulates the frozen sequence: A reads gen N; B adds
        EU16 through a full second Graphiti pipeline over its own connection;
        C replays an as-of read; A issues a stale write 'against gen N'."""
        seq: dict[str, Any] = {}
        err: str | None = None
        try:
            gen_n_res = await self.driver.execute_query(
                "MATCH (e:Episodic {group_id: $g}) RETURN count(e) AS n", g=self.group_id)
            gen_n = gen_n_res[0][0]["n"]
            seq["A_reads_gen_N"] = gen_n

            if self.second_session_add is None:
                raise RuntimeError("second-session factory not wired into evaluator")
            add_result = await self.second_session_add(
                name="EU16 (S1)",
                episode_body="Project Alphard budget revised to 9 million dollars.",
                reference_time=dt_instant("2026-02-04T00:00:00Z"),
            )
            seq["B_commits_new_evidence"] = {
                "nodes": len(add_result.nodes), "edges": len(add_result.edges)}

            replay = await self.driver.execute_query(
                "MATCH (e:Episodic {group_id: $g}) RETURN count(e) AS n",
                g=self.group_id)
            seq["C_replay_count_after_B"] = replay[0][0]["n"]

            stale = await self.driver.execute_query(
                "CREATE (s:Episodic {group_id: $g, uuid: 'stale-a-gen-n', "
                "name: 'stale-A-write-against-gen-N'}) RETURN s.uuid AS uuid",
                g=self.group_id)
            accepted = bool(stale and stale[0])
            await self.driver.execute_query(
                "MATCH (s:Episodic {uuid: 'stale-a-gen-n'}) DETACH DELETE s")
            seq["A_stale_write_against_gen_N"] = {
                "observed": "accepted silently" if accepted else "rejected",
                "expected_if_versioned": "rejected or explicitly versioned",
                "isolation_level_found": "AUTOCOMMIT per query; lost-update window",
            }
            verdict = ("observed: stale write ACCEPTED silently; graphiti provides no "
                       "concurrency control over FalkorDB; behavior recorded per frozen spec"
                       if accepted else
                       "observed: stale write rejected by the backend; isolation "
                       "behavior recorded per frozen spec")
        except Exception as e:  # noqa: BLE001 - record, never hide
            err = f"{type(e).__name__}: {e}"
            seq["runtime_probe"] = "BLOCKED (see runtime_error)"
            seq["static_isolation_level"] = (
                "AUTOCOMMIT-per-query; no transactions/read-snapshots/optimistic "
                "concurrency in graphiti-core 0.29.3 FalkorDriver (base no-op tx)")
            verdict = "runtime sequence blocked before observation; static finding recorded"
        untestable_runtime = err is not None
        return case_result(
            "X14",
            {"sequence": seq, "runtime_error": err, "conclusion": verdict},
            ok=(None if untestable_runtime else True),
            failure_class="F-conc" if untestable_runtime else None,
            notes="needs live endpoint for the full four-step runtime sequence"
                  if untestable_runtime else None)
