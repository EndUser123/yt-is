"""Open-world horizon scouting: thin client over the existing search fleet.

Scout queries describe CATEGORIES / PROBLEMS / GOALS derived from the
personal interest graph; the SEARCH RESULTS supply the unknown entity
names. This module never names specific products itself, and it never
runs its own crawler or search service — it is a thin client to the
existing local search fleet (search_web MCP server on 127.0.0.1:8323).

Planning (build_scout_plan) is a pure function of the graph DB state and
performs ZERO network calls. Execution (run_scout) is gated behind an
explicit allow_search flag, spends only free tiers (fast/medium/deep),
and never fabricates results: transport failures surface as
ScoutUnavailable (or per-query error entries), never as made-up records.

EXPLORATION_BUDGET values are the INITIAL policy, not an optimal
allocation; adjacent (0.2) and wildcard (0.1) shares exist so the system
cannot collapse into a filter bubble around what is already known.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable

SCOUT_POLICY_VERSION = "horizon-scout-v1"
EXPLORATION_BUDGET = {"known_domain": 0.7, "adjacent": 0.2, "wildcard": 0.1}
INTENT_FAMILIES = (
    "emerging_projects",
    "new_methods",
    "new_research",
    "alternatives",
    "rapidly_growing_tools",
    "architecture_changes",
)
SEARCH_WEB_URL = "http://127.0.0.1:8323/mcp"
_VALID_TIERS = ("fast", "medium", "deep")

# Category query templates. The query names the CATEGORY; the unknown
# entity names come from the search results, never from this module.
INTENT_TEMPLATES = {
    "emerging_projects": "new open source projects {domain}",
    "new_methods": "emerging {domain} methods and techniques",
    "new_research": "new research papers {domain}",
    "alternatives": "alternatives to common {domain} tools",
    "rapidly_growing_tools": "rapidly growing open source {domain} tools",
    "architecture_changes": "recent architecture changes in {domain}",
}

# Fixed generic wildcard categories: no personal entity names, no
# specific product names. These keep the scout from becoming a filter
# bubble regardless of what the interest graph contains.
WILDCARD_QUERIES = (
    "rapidly growing open source developer tools 2026",
    "new open source agent orchestration runtimes",
    "emerging programming languages and runtimes",
    "new developer tooling projects",
)

_EXPLORATION_RANK = {"known_domain": 0, "adjacent": 1, "wildcard": 2}
_READ_INTEREST_STATES = ("durable", "active", "current_problem")
_MAX_WORDS_DOMAIN = 8  # domains describe a PROBLEM, not an essay
_PER_ORIGIN_CAP = 2


class ScoutUnavailable(RuntimeError):
    """Explicit transport failure. Never fabricate results in its place."""


@dataclass(frozen=True)
class ScoutQuery:
    query_id: str
    origin_kind: str  # interest|goal|information_need|wildcard
    origin_id: str | None
    domain: str
    intent: str
    query: str
    exploration: str  # known_domain|adjacent|wildcard
    policy_version: str


@dataclass(frozen=True)
class ScoutPlan:
    plan_id: str
    created_at: str
    policy_version: str
    queries: tuple[ScoutQuery, ...]

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "policy_version": self.policy_version,
            "queries": [asdict(q) for q in self.queries],
        }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short_hash(text: str, n: int = 10) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def _normalize_query_text(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text.casefold()).strip()
    return re.sub(r"[^\w ]+", "", collapsed)


def _domain_from_text(text: str) -> str:
    words = re.sub(r"\s+", " ", text.strip()).split(" ")
    return " ".join(words[:_MAX_WORDS_DOMAIN])


# ---------------------------------------------------------------------------
# Planning (zero network calls; pure function of DB state)
# ---------------------------------------------------------------------------


def _open_graph(graph_db: Any) -> sqlite3.Connection | None:
    """Open the graph DB read-only when given a path; never write to it."""
    if isinstance(graph_db, sqlite3.Connection):
        return graph_db
    path = Path(graph_db)
    if not path.exists():
        return None
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_sources(conn: sqlite3.Connection | None) -> list[tuple[str, str, str]]:
    """(origin_kind, origin_id, domain) triples, deterministically sorted."""
    sources: list[tuple[str, str, str]] = []
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT interest_id, name FROM interests WHERE temporal_state"
                " IN (?,?,?) ORDER BY interest_id",
                _READ_INTEREST_STATES,
            ).fetchall()
            for r in rows:
                if r["name"]:
                    sources.append(("interest", r["interest_id"], _domain_from_text(r["name"])))
        except sqlite3.Error:
            pass
        try:
            rows = conn.execute(
                "SELECT goal_id, statement FROM goals ORDER BY goal_id"
            ).fetchall()
            for r in rows:
                if r["statement"]:
                    sources.append(("goal", r["goal_id"], _domain_from_text(r["statement"])))
        except sqlite3.Error:
            pass
        try:
            rows = conn.execute(
                "SELECT need_id, statement FROM information_needs ORDER BY need_id"
            ).fetchall()
            for r in rows:
                if r["statement"]:
                    sources.append(
                        ("information_need", r["need_id"], _domain_from_text(r["statement"]))
                    )
        except sqlite3.Error:
            pass
    sources.sort(key=lambda s: (s[0], s[1]))
    return sources


def _allocate_slots(max_queries: int, budget: dict) -> tuple[int, int, int]:
    """Rounded per-exploration slot counts with the anti-bubble invariant:
    adjacent>=1 and wildcard>=1 whenever max_queries>=6."""
    known = round(max_queries * budget["known_domain"])
    adjacent = round(max_queries * budget["adjacent"])
    wildcard = round(max_queries * budget["wildcard"])
    if max_queries >= 6:
        if adjacent < 1:
            adjacent = 1
        if wildcard < 1:
            wildcard = 1
        if known < adjacent:
            known = adjacent
    while known + adjacent + wildcard > max_queries and known > 0:
        known -= 1
    return known, adjacent, wildcard


def _pool_queries(
    sources: list[tuple[str, str, str]], start_index: int, limit: int
) -> list[ScoutQuery]:
    """Category queries from graph sources; at most 2 per origin, lexically
    deduped, deterministic."""
    out: list[ScoutQuery] = []
    seen: set[str] = set()
    per_origin: dict[str, int] = {}
    counter = start_index
    for kind, origin_id, domain in sources:
        if len(out) >= limit:
            break
        made = per_origin.get(origin_id, 0)
        while made < _PER_ORIGIN_CAP and len(out) < limit:
            intent = INTENT_FAMILIES[counter % len(INTENT_FAMILIES)]
            counter += 1
            text = INTENT_TEMPLATES[intent].format(domain=domain)
            norm = _normalize_query_text(text)
            if norm not in seen:
                seen.add(norm)
                out.append(
                    ScoutQuery(
                        query_id="q_" + _short_hash(text),
                        origin_kind=kind,
                        origin_id=origin_id,
                        domain=domain,
                        intent=intent,
                        query=text,
                        exploration="",  # set by caller
                        policy_version=SCOUT_POLICY_VERSION,
                    )
                )
            made += 1
        per_origin[origin_id] = made
    return out


def _wildcard_queries(limit: int) -> list[ScoutQuery]:
    out: list[ScoutQuery] = []
    for i in range(min(limit, len(WILDCARD_QUERIES))):
        text = WILDCARD_QUERIES[i]
        intent = INTENT_FAMILIES[(i + 4) % len(INTENT_FAMILIES)]
        out.append(
            ScoutQuery(
                query_id="q_" + _short_hash(text),
                origin_kind="wildcard",
                origin_id=None,
                domain="developer tools",
                intent=intent,
                query=text,
                exploration="wildcard",
                policy_version=SCOUT_POLICY_VERSION,
            )
        )
    return out


def build_scout_plan(
    graph_db: Any,
    *,
    now: str | None = None,
    max_queries: int = 12,
    budget: dict = EXPLORATION_BUDGET,
) -> ScoutPlan:
    """Build a deterministic scout plan from the personal graph DB.

    Queries name CATEGORIES derived from interests/goals/needs; the
    results supply the unknown names. Pure function of DB state plus
    max_queries: no network, no clock in the identity (plan_id depends
    only on the query set). Missing/empty tables degrade to wildcards.
    """
    conn = _open_graph(graph_db)
    try:
        sources = _fetch_sources(conn)
    finally:
        if conn is not None and graph_db is not conn:
            conn.close()

    interest_sources = [s for s in sources if s[0] == "interest"]
    adjacent_sources = [s for s in sources if s[0] in ("goal", "information_need")]
    if not adjacent_sources:
        adjacent_sources = interest_sources

    n_known, n_adjacent, n_wildcard = _allocate_slots(max_queries, budget)
    known = [
        q.__class__(**{**asdict(q), "exploration": "known_domain"})
        for q in _pool_queries(interest_sources, 0, n_known)
    ]
    adjacent = [
        q.__class__(**{**asdict(q), "exploration": "adjacent"})
        for q in _pool_queries(adjacent_sources, 3, n_adjacent)
    ]
    # Unfillable known/adjacent slots become MORE exploration, never less.
    shortfall = (n_known - len(known)) + (n_adjacent - len(adjacent))
    wildcard = _wildcard_queries(n_wildcard + max(0, shortfall))

    queries = sorted(
        known + adjacent + wildcard,
        key=lambda q: (_EXPLORATION_RANK[q.exploration], q.origin_id or "", q.query),
    )
    plan_id = "plan_" + _short_hash(
        "\n".join(sorted(q.query_id for q in queries)), 12
    )
    return ScoutPlan(
        plan_id=plan_id,
        created_at=now if now is not None else _now_iso(),
        policy_version=SCOUT_POLICY_VERSION,
        queries=tuple(queries),
    )


# ---------------------------------------------------------------------------
# Execution (spend-gated, free tiers only, fail-soft per query)
# ---------------------------------------------------------------------------


def _rpc(url: str, payload: dict, session: str | None, timeout: float) -> tuple[dict, str | None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["mcp-session-id"] = session
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            sid = resp.headers.get("mcp-session-id")
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ScoutUnavailable(f"MCP transport failure: {exc}") from exc
    data: dict = {}
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                data = json.loads(line[5:])
                break
            except ValueError:
                continue
    if not data:
        raise ScoutUnavailable("MCP response contained no parsable SSE data line")
    return data, sid


def _extract_records(payload: Any) -> list[dict]:
    """Pull result records (dicts carrying a url) out of a parsed payload."""
    records: list[dict] = []
    if isinstance(payload, list):
        for item in payload:
            records.extend(_extract_records(item))
    elif isinstance(payload, dict):
        if "url" in payload:
            records.append(payload)
        for key in ("results", "items", "data"):
            if key in payload:
                records.extend(_extract_records(payload[key]))
    return records


def _default_mcp_call(
    tool: str,
    arguments: dict,
    *,
    url: str = SEARCH_WEB_URL,
    timeout: float = 90.0,
) -> list[dict]:
    """Default transport seam: initialize -> tools/call against the existing
    search_web MCP server. Reuses the proven client pattern; this is NOT a
    new search service."""
    init, session = _rpc(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "horizon-scout", "version": "1.0"},
            },
        },
        None,
        timeout,
    )
    if init.get("error") is not None:
        raise ScoutUnavailable(f"MCP initialize error: {init['error']}")
    resp, _ = _rpc(
        url,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        session,
        timeout,
    )
    result = resp.get("result")
    if not isinstance(result, dict) or result.get("isError"):
        raise ScoutUnavailable(f"MCP tools/call failed for {tool!r}: {resp.get('error', result)}")
    records: list[dict] = []
    for item in result.get("content", []):
        if not isinstance(item, dict) or "text" not in item:
            continue
        try:
            parsed = json.loads(item["text"])
        except (TypeError, ValueError):
            continue
        records.extend(_extract_records(parsed))
    return records


def _backend_of(record: dict) -> str:
    if isinstance(record.get("backend"), str) and record["backend"]:
        return record["backend"]
    backends = record.get("rrf_backends")
    if isinstance(backends, list) and backends:
        return "+".join(str(b) for b in backends)
    return "search_web"


def run_scout(
    plan: ScoutPlan,
    *,
    mcp_call: Callable[[str, dict], list[dict]] | None = None,
    allow_search: bool = False,
    tier: str = "fast",
    num_results: int = 8,
) -> list[dict]:
    """Execute a scout plan against the search fleet (existing service, thin
    client). Spend gate: allow_search=False raises PermissionError. Only free
    tiers; 'pro' (or anything else) raises ValueError. Fail-soft per query:
    one transport failure yields an error entry, never fabricated records;
    failure of every query propagates ScoutUnavailable."""
    if not allow_search:
        raise PermissionError("scout-run requires --allow-search")
    if tier not in _VALID_TIERS:
        raise ValueError(f"tier must be one of {_VALID_TIERS}, got {tier!r}")
    seam = mcp_call if mcp_call is not None else partial(_default_mcp_call)

    out: list[dict] = []
    failures = 0
    last_exc: ScoutUnavailable | None = None
    for q in plan.queries:
        try:
            raw = seam("query", {"search_query": q.query, "num_results": num_results, "tier": tier})
        except (ScoutUnavailable, OSError, ValueError) as exc:
            failures += 1
            last_exc = exc if isinstance(exc, ScoutUnavailable) else ScoutUnavailable(str(exc))
            out.append({"query_id": q.query_id, "error": str(exc)})
            continue
        for rec in raw:
            if not isinstance(rec, dict) or not rec.get("url"):
                continue  # no URL -> no verifiable entity; drop, never invent
            out.append(
                {
                    "query_id": q.query_id,
                    "query": q.query,
                    "backend": _backend_of(rec),
                    "title": str(rec.get("title") or ""),
                    "url": str(rec["url"]),
                    "snippet": str(rec.get("snippet") or ""),
                }
            )
    if plan.queries and failures == len(plan.queries) and last_exc is not None:
        raise last_exc
    return out


# ---------------------------------------------------------------------------
# Normalization and ingestion
# ---------------------------------------------------------------------------

_GITHUB_HOSTS = {"github.com", "www.github.com", "m.github.com"}


def normalize_github_repo(url: str) -> tuple[str, str] | None:
    """Return ("owner/repo" lowercased, canonical https URL) for a GitHub
    repo URL in common shapes, else None. Identity is owner/repo, NOT the
    page title."""
    if not isinstance(url, str):
        return None
    try:
        parts = urllib.request.urlparse(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.hostname is None:
        return None
    if parts.hostname.lower() not in _GITHUB_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not owner or not repo:
        return None
    owner_l, repo_l = owner.lower(), repo.lower()
    return f"{owner_l}/{repo_l}", f"https://github.com/{owner_l}/{repo_l}"


def ingest_external_results(
    registry_conn: sqlite3.Connection,
    results: list[dict],
    *,
    run_id: str,
    observed_at: str | None = None,
) -> dict:
    """Ingest scout results into the ef.concept_registry (existing API,
    unmodified). GitHub results become repository concepts: canonical_name
    is owner/repo derived from the URL (the display title is only an
    alias). Non-GitHub results are counted and skipped (registry sink is
    repositories in v1). Same repo across different queries joins the same
    deterministic concept as separate observations."""
    from ef import concept_registry as cr

    ts = observed_at if observed_at is not None else _now_iso()
    seen_concepts: set[str] = set()
    observations = 0
    skipped = 0
    for r in results:
        url = r.get("url")
        if not url:
            continue  # error entries carry no url and no entity
        norm = normalize_github_repo(str(url))
        if norm is None:
            skipped += 1
            continue
        canonical, canonical_url = norm
        concept_id = cr.upsert_concept(
            registry_conn,
            canonical,
            "repository",
            lifecycle_state="candidate",
            user_relationship="unknown",
        )
        title = str(r.get("title") or canonical)
        cr.add_alias(registry_conn, concept_id, title)
        cr.record_observation(
            registry_conn,
            concept_id,
            source_kind="web_search",
            source_id=f"{r.get('query_id', 'unknown')}:{r.get('backend', 'search_web')}",
            source_url=canonical_url,
            title=title,
            snippet=str(r.get("snippet") or ""),
            observed_at=ts,
            run_id=run_id,
            metadata={
                "backend": r.get("backend", "search_web"),
                "query": r.get("query", ""),
                "exploration": r.get("exploration", ""),
            },
        )
        seen_concepts.add(concept_id)
        observations += 1
    return {
        "concepts": len(seen_concepts),
        "observations": observations,
        "skipped_non_github": skipped,
    }


# ---------------------------------------------------------------------------
# Novelty
# ---------------------------------------------------------------------------


def check_novelty(
    registry_conn: sqlite3.Connection,
    canonical_name: str,
    *,
    mcp_call: Callable[[str, dict], list[dict]] | None = None,
) -> dict:
    """Novelty triage for a canonical name. Registry lookup is exact.
    Corpus check goes through the search fleet seam (search_all); transport
    or parse failure yields "unknown" — never a guess. This does NOT claim
    anything about the user (no "new to user" assertions here)."""
    from ef import concept_registry as cr

    concept_id = cr.resolve_alias(registry_conn, canonical_name)
    if concept_id is None:
        row = registry_conn.execute(
            "SELECT concept_id FROM concepts WHERE canonical_name = ? LIMIT 1",
            (canonical_name,),
        ).fetchone()
        concept_id = row["concept_id"] if row else None
    new_to_registry = concept_id is None

    new_to_corpus: bool | str = "unknown"
    if mcp_call is not None:
        try:
            records = mcp_call("search_all", {"search_query": canonical_name, "limit": 5})
            needle = canonical_name.casefold()
            mentioned = any(
                needle in str(r.get("title", "")).casefold()
                or needle in str(r.get("snippet", "")).casefold()
                for r in records
                if isinstance(r, dict)
            )
            new_to_corpus = not mentioned
        except (ScoutUnavailable, OSError, ValueError):
            new_to_corpus = "unknown"
    return {
        "new_to_registry": new_to_registry,
        "new_to_corpus": new_to_corpus,
        "previously_known": not new_to_registry,
    }
