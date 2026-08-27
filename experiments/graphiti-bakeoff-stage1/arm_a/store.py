"""Arm A adapter: existing yt-is relational substrate + minimum added machinery.

REUSED (existing mechanisms, imported from ef/concept_registry.py):
  - concept_identity_id / upsert_concept  -> deterministic concept identity
  - normalize_alias / add_alias / resolve_alias -> exact-normalized alias identity
  - record_observation (evidence_ref provenance) -> per-EU observation rows
  - record_concept_relation -> current-state relation projection with evidence
  - ensure_schema / _short_digest -> idempotent registry tables, deterministic ids
Pattern precedent: ef/personal_graph.py content-hash identity + tmp-DB test init.
Registry tables are created in THIS adapter's own DB, never the production catalog.

ADDED (stage-0 inventory said ABSENT in production code; every function below is
new semantic machinery; schema prefix ea_ marks adapter-owned tables):
  1. ea_evidence_units      - evidence table with t + removed tombstone
  2. ea_assertions          - versioned assertions: valid_from/valid_to,
                              supersedes/superseded_by links, claim_key lineage
  3. ea_removals            - removal log (non-destructive audit)
  4. ea_entity_map          - fixture entity_id <-> concept_id glue
  5. ea_meta                - generation counter for optimistic concurrency
  6. supersession at ingest (_supersede_target + demotion inside apply_eu)
  7. as-of visibility + support/emergence per claim lineage (as_of_query)
  8. provenance(), supersession_history()
  9. bridge discovery with aggregate source support + why_surfaced
 10. evidence-removal downstream downgrade (remove_eu)
 11. replay to checkpoints from stored fixture (replay)
 12. guarded_apply_eu optimistic-concurrency write (StaleWriteError)

Line-accounting split: this file counts WHOLLY as added semantic machinery
(every capability was absent pre-bakeoff), except that calls into ef.concept_registry
are reuse, not addition. cases.py / run_stage1.py count as harness.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ef import concept_registry as cr  # noqa: E402  (reused substrate)

NOW_SENTINEL = "9999-12-31T00:00:00Z"

ARM_A_SCHEMA = """
CREATE TABLE IF NOT EXISTS ea_evidence_units (
  eu_id TEXT PRIMARY KEY,
  t TEXT NOT NULL,
  source_id TEXT NOT NULL,
  channel TEXT,
  text TEXT,
  removed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ea_assertions (
  assertion_id TEXT PRIMARY KEY,
  eu_id TEXT NOT NULL REFERENCES ea_evidence_units(eu_id),
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object TEXT NOT NULL,
  source_id TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  supersedes_assertion TEXT,
  superseded_by TEXT,
  claim_key TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ea_assertions_sp ON ea_assertions(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_ea_assertions_claim ON ea_assertions(claim_key);
CREATE TABLE IF NOT EXISTS ea_removals (
  eu_id TEXT PRIMARY KEY,
  removed_at TEXT,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS ea_entity_map (
  entity_id TEXT PRIMARY KEY,
  concept_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ea_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


class StaleWriteError(RuntimeError):
    """Optimistic concurrency guard rejected a write held against a stale generation."""


def _t(value):  # timestamps are fixed-format ISO Z; string compare is correct
    return str(value)


class ArmAStore:
    """Isolated arm DB holding reused registry tables + added ea_ temporal tables."""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else None
        if self.db_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            self.conn.execute("PRAGMA journal_mode=WAL")
        else:
            self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        cr.ensure_schema(self.conn)          # REUSE: registry tables in our DB
        self.conn.executescript(ARM_A_SCHEMA)  # ADDED tables
        self.conn.execute(
            "INSERT OR IGNORE INTO ea_meta (key, value) VALUES ('generation', '0')")
        self.conn.commit()
        self._fixture = None
        self._cid_cache = {}
        self._source_channels: dict[str, str] = {}

    def close(self):
        self.conn.close()

    # -- ingest ------------------------------------------------------------

    def load_entities(self, entities):
        """REUSES cr.upsert_concept + cr.add_alias (deterministic identity, aliases)."""
        declared = []
        for ent in entities:
            cid = cr.upsert_concept(
                self.conn, ent["canonical_name"], ent["type"],
                metadata={"entity_id": ent["entity_id"]})
            for alias in [ent["canonical_name"], *ent.get("aliases", [])]:
                cr.add_alias(self.conn, cid, alias)
            self.conn.execute(
                "INSERT OR IGNORE INTO ea_entity_map (entity_id, concept_id)"
                " VALUES (?,?)", (ent["entity_id"], cid))
            declared.append(ent["entity_id"])
        self.conn.commit()
        return declared

    def cid_of(self, entity_id):
        if entity_id not in self._cid_cache:
            row = self.conn.execute(
                "SELECT concept_id FROM ea_entity_map WHERE entity_id=?",
                (entity_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown entity_id: {entity_id}")
            self._cid_cache[entity_id] = row["concept_id"]
        return self._cid_cache[entity_id]

    def resolve_name(self, alias):
        """REUSES cr.resolve_alias (exact normalized); maps back to fixture ids."""
        cid = cr.resolve_alias(self.conn, alias)
        if cid is None:
            return None
        row = self.conn.execute(
            "SELECT entity_id FROM ea_entity_map WHERE concept_id=?", (cid,)).fetchone()
        return row["entity_id"] if row else None

    def _supersede_target(self, subject, predicate, supersedes_value):
        """ADDED: locate the prior assertion a supersedes marker replaces."""
        if supersedes_value is None:
            return None
        row = self.conn.execute(
            "SELECT assertion_id, claim_key FROM ea_assertions WHERE subject=?"
            " AND predicate=? AND object=? AND active=1"
            " ORDER BY valid_from DESC LIMIT 1",
            (subject, predicate, str(supersedes_value))).fetchone()
        if row:
            return dict(row)
        row = self.conn.execute(  # guarded fallback: latest same property
            "SELECT assertion_id, claim_key FROM ea_assertions WHERE subject=?"
            " AND predicate=? AND active=1 ORDER BY valid_from DESC LIMIT 1",
            (subject, predicate)).fetchone()
        return dict(row) if row else None

    def apply_eu(self, eu, expected_generation=None):
        """ADDED: evidence-unit ingest incl. supersession, observations, relations.

        expected_generation: optimistic token (ADDED). When given and the store's
        generation has moved on, raises StaleWriteError before any write lands.
        """
        if expected_generation is not None:
            cur = self.conn.execute(
                "UPDATE ea_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT)"
                " WHERE key='generation' AND CAST(value AS INTEGER)=?",
                (expected_generation,))
            if cur.rowcount == 0:
                raise StaleWriteError(
                    f"stale write: held generation {expected_generation},"
                    f" store generation is {self.generation}")
        else:
            self.conn.execute(
                "UPDATE ea_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT)"
                " WHERE key='generation'")
        self.conn.execute(
            "INSERT OR IGNORE INTO ea_evidence_units (eu_id,t,source_id,channel,text,removed)"
            " VALUES (?,?,?,?,?,0)",
            (eu["eu_id"], _t(eu["t"]), eu["source_id"],
             eu.get("channel"), eu.get("text")))
        src_row = self.conn.execute(
            "SELECT channel FROM ea_evidence_units WHERE eu_id=?", (eu["eu_id"],)).fetchone()
        # Fixture encoding: 'asserts' mixes real assertion dicts with
        # annotation dicts like {"supersedes_value": "2031"}; a lone marker
        # binds to the assertion at its list position.
        plain = [a for a in eu["asserts"] if "subject" in a]
        markers = [a.get("supersedes_value") for a in eu["asserts"]
                   if "supersedes_value" in a]
        new_asserts = []
        for idx, a in enumerate(plain):
            subject, predicate, obj = a["subject"], a["predicate"], str(a["object"])
            sup_val = a.get("supersedes_value")
            if sup_val is None and idx < len(markers):
                sup_val = markers[idx]
            aid = "asr_" + cr._short_digest(eu["eu_id"], subject, predicate, obj)
            target = self._supersede_target(subject, predicate, sup_val)
            claim_key = (
                target["claim_key"] if target
                else "clm_" + cr._short_digest(subject, predicate, obj))
            # ADDED: versioned assertion row; historical rows are never overwritten.
            self.conn.execute(
                "INSERT INTO ea_assertions (assertion_id,eu_id,subject,predicate,object,"
                " source_id,valid_from,valid_to,active,supersedes_assertion,superseded_by,"
                "claim_key) VALUES (?,?,?,?,?,?,?,NULL,1,?,NULL,?)",
                (aid, eu["eu_id"], subject, predicate, obj, eu["source_id"],
                 _t(eu["t"]), target["assertion_id"] if target else None, claim_key))
            if target:  # ADDED: non-destructive demotion of the prior version
                self.conn.execute(
                    "UPDATE ea_assertions SET active=0, valid_to=?, superseded_by=?"
                    " WHERE assertion_id=?",
                    (_t(eu["t"]), aid, target["assertion_id"]))
            new_asserts.append((aid, subject, predicate, obj))
        involved = []
        for aid2, s, p, o in new_asserts:
            for eid in (s, o):
                if eid in self.predeclared_entity_ids() and eid not in involved:
                    involved.append(eid)
        for eid in involved:  # REUSE: cr.record_observation w/ evidence_ref provenance
            cr.record_observation(
                self.conn, self.cid_of(eid), source_kind="fixture_source",
                source_id=eu["source_id"], observed_at=_t(eu["t"]),
                snippet=(eu.get("text") or "")[:240], evidence_ref=eu["eu_id"],
                metadata={"channel": src_row["channel"], "eu_id": eu["eu_id"]})
        for aid3, s3, p3, o3 in new_asserts:  # REUSE: cr.record_concept_relation
            if s3 in self.predeclared_entity_ids() and o3 in self.predeclared_entity_ids():
                cr.record_concept_relation(
                    self.conn, self.cid_of(s3), self.cid_of(o3), p3, 1.0,
                    method="evidence",
                    evidence={"eu_ids": [eu["eu_id"]], "t": _t(eu["t"])})
        self.conn.commit()
        return len(new_asserts)

    def predeclared_entity_ids(self):
        return {r["entity_id"] for r in
                self.conn.execute("SELECT entity_id FROM ea_entity_map").fetchall()}

    def _fixture_eu_t(self):
        """Harness helper: [(eu_id, t)] from the stored fixture."""
        if self._fixture is None:
            return []
        return [(u["eu_id"], _t(u["t"])) for u in self._fixture["evidence_units"]]

    def ingest_fixture(self, fixture):
        """Load entities then EU stream in ascending t order; keep fixture for replay."""
        self.load_entities(fixture["entities"])
        self._fixture = fixture
        self._source_channels = {
            s["source_id"]: s.get("channel", "") for s in fixture.get("sources", [])}
        eus = sorted(fixture["evidence_units"], key=lambda u: _t(u["t"]))
        n = 0
        for eu in eus:
            n += self.apply_eu({**eu, "channel": self._source_channels.get(eu["source_id"])})
        return {"entities": len(fixture["entities"]), "evidence_units": len(eus),
                "assertions": n}

    @property
    def generation(self):
        row = self.conn.execute(
            "SELECT value FROM ea_meta WHERE key='generation'").fetchone()
        return int(row["value"])

    def guarded_apply_eu(self, eu, expected_generation):
        """ADDED: public optimistic-concurrency entry point used by the X14 sim."""
        return self.apply_eu(eu, expected_generation=expected_generation)

    # -- query API ---------------------------------------------------------

    def _chain_rows(self, subject=None, predicate=None, as_of=None, object_=None):
        """ADDED: all lineage assertions visible-so-far (valid_from <= T, EU live).

        One SQL tier feeds both status computation (history) and window filtering
        (current values), so support can never disagree with the visible timeline.
        """
        sql = ("SELECT a.*, e.removed AS removed FROM ea_assertions a"
               " JOIN ea_evidence_units e ON e.eu_id = a.eu_id"
               " WHERE e.removed = 0 AND a.valid_from <= ?")
        params = [_t(as_of) if as_of else NOW_SENTINEL]
        if subject is not None:
            sql += " AND a.subject = ?"; params.append(subject)
        if predicate is not None:
            sql += " AND a.predicate = ?"; params.append(predicate)
        if object_ is not None:
            sql += " AND a.object = ?"; params.append(str(object_))
        sql += " ORDER BY a.valid_from, a.assertion_id"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    @staticmethod
    def _window_visible(row, as_of):
        T = _t(as_of) if as_of else NOW_SENTINEL
        return row["valid_from"] <= T and (row["valid_to"] is None or row["valid_to"] > T)

    @staticmethod
    def _support(rows):
        """ADDED: SUPPORTED iff >=2 distinct sources; emergence = walk-to-second-source."""
        seen_sources, ordered = [], []
        for r in sorted(rows, key=lambda x: (x["valid_from"], x["eu_id"])):
            ordered.append(r)
            if r["source_id"] not in seen_sources:
                seen_sources.append(r["source_id"])
            if len(seen_sources) >= 2:
                return {"supported": True, "emergence": r["valid_from"],
                        "sources": sorted(set(seen_sources)),
                        "lineage_eus": [x["eu_id"] for x in ordered]}
        return {"supported": False, "emergence": None, "sources": sorted(set(seen_sources)),
                "lineage_eus": [x["eu_id"] for x in ordered]}

    def as_of_query(self, subject=None, predicate=None, object_=None, as_of=None):
        """ADDED: live as-of view (production only had re-run discovery).

        Returns one entry per claim lineage. Status = support over the chain
        visible-so-far; current_values = chain members whose validity window
        contains T. Contradictions are separate lineages and coexist.
        """
        rows = self._chain_rows(subject=subject, predicate=predicate,
                                object_=object_, as_of=as_of)
        by_claim: dict[str, list[dict]] = {}
        for r in rows:
            by_claim.setdefault(r["claim_key"], []).append(r)
        entries = []
        for ck, crow in by_claim.items():
            sup = self._support(crow)
            window = [r for r in crow if self._window_visible(r, as_of)]
            values = {}
            for r in window:
                slot = values.setdefault(r["object"], [])
                if r["eu_id"] not in slot:
                    slot.append(r["eu_id"])
            entries.append({
                "claim_key": ck,
                "subject": crow[0]["subject"],
                "predicate": crow[0]["predicate"],
                "status": "SUPPORTED" if sup["supported"] else "ASSERTED_ONLY",
                "emergence": sup["emergence"],
                "sources": sup["sources"],
                "lineage_eus": sup["lineage_eus"],
                "current_values": [
                    {"value": v, "backed_by": backs} for v, backs in sorted(values.items())],
            })
        entries.sort(key=lambda e: (e["subject"], e["predicate"], e["claim_key"]))
        return entries

    def provenance(self, subject, predicate, object_=None, as_of=None):
        """ADDED: exact supporting-EU set for an assertion/claim (X12 semantics)."""
        rows = self._chain_rows(subject=subject, predicate=predicate,
                                as_of=as_of, object_=object_)
        detail = [{"eu_id": r["eu_id"], "t": r["valid_from"],
                   "source_id": r["source_id"], "value": r["object"]} for r in rows]
        eus, seen = [], set()
        for d in detail:
            if d["eu_id"] not in seen:
                seen.add(d["eu_id"]); eus.append(d["eu_id"])
        return {"eu_ids": eus, "detail": detail}

    def supersession_history(self, subject, predicate, as_of=None):
        """ADDED: edge version history (absent in production code)."""
        rows = self._chain_rows(subject=subject, predicate=predicate, as_of=as_of)
        chains = {}
        for r in rows:
            chains.setdefault(r["claim_key"], []).append(r)
        out = []
        for ck, crow in chains.items():
            has_link = any(c["superseded_by"] or c["supersedes_assertion"] for c in crow)
            if not has_link:
                continue
            out.append({
                "claim_key": ck,
                "versions": [
                    {"value": c["object"], "eu_id": c["eu_id"],
                     "valid_from": c["valid_from"], "valid_to": c["valid_to"],
                     "active": bool(c["active"]),
                     "superseded_by": c["superseded_by"]}
                    for c in sorted(crow, key=lambda x: x["valid_from"])],
            })
        return out

    # -- bridges -----------------------------------------------------------

    def _live_edges(self, as_of=None):
        rows = self._chain_rows(as_of=as_of)
        ent = self.predeclared_entity_ids()
        edges = {}
        for r in rows:
            if r["subject"] in ent and r["object"] in ent:
                key = (r["subject"], r["predicate"], r["object"])
                edges.setdefault(key, []).append(r)
        return edges

    def find_bridges(self, topic_a, topic_b, as_of=None):
        """ADDED: 2-hop path discovery with aggregate source support + explanation."""
        edges = self._live_edges(as_of)
        adj: dict[str, list[tuple]] = {}
        for (s, p, o) in edges:
            adj.setdefault(s, []).append((o, s, p))
            adj.setdefault(o, []).append((s, o, p))
        nb_a = {t[0] for t in adj.get(topic_a, [])}
        nb_b = {t[0] for t in adj.get(topic_b, [])}
        mediators = (nb_a & nb_b) - {topic_a, topic_b}
        name_cache = self._name_cache()
        results = []
        for m in sorted(mediators):
            path_edges = [k for k in edges if {k[0], k[2]} == {topic_a, m}] + \
                         [k for k in edges if {k[0], k[2]} == {m, topic_b}]
            left_edges = [k for k in edges if {k[0], k[2]} == {topic_a, m}]
            right_edges = [k for k in edges if {k[0], k[2]} == {m, topic_b}]
            support_rows = [r for k in path_edges for r in edges[k]]
            # Aggregate-support model: the far hop must contribute an
            # independent source NOT already present on the near side;
            # otherwise the pair of hops carries no joint information and is
            # not a bridge (a node merely related to both topics directly
            # with >=2 sources on one side is not surfaced).
            src_left = {r["source_id"] for k in left_edges for r in edges[k]}
            src_right = {r["source_id"] for k in right_edges for r in edges[k]}
            agg = self._aggregate_support(support_rows)
            adds_source = len(agg["sources"]) > max(len(src_left), len(src_right))
            if not (agg["supported"] and adds_source):
                continue
            legs = []
            for k in path_edges:
                s, p, o = k
                ns, no = name_cache.get(s, s), name_cache.get(o, o)
                legs.append(f"{ns} <-{p}- {name_cache.get(m, m)}"
                            if s != m else f"{ns} -{p}-> {no}")
            why = self._why_surfaced(m, topic_a, topic_b, support_rows, agg, legs, name_cache)
            results.append({
                "mediator": m,
                "path": legs,
                "status": "SUPPORTED" if agg["supported"] else "ASSERTED_ONLY",
                "emergence": agg["emergence"],
                "aggregate_sources": agg["sources"],
                "supporting_eus": list(dict.fromkeys(agg["lineage_eus"])),
                "why_surfaced": why,
            })
        return results

    def _name_cache(self):
        cache = {}
        for row in self.conn.execute(
                "SELECT m.entity_id, c.canonical_name FROM ea_entity_map m"
                " JOIN concepts c ON c.concept_id = m.concept_id"):
            cache[row["entity_id"]] = row["canonical_name"]
        return cache

    @staticmethod
    def _aggregate_support(support_rows):
        """ADDED: support at aggregate (multi-hop) level across independent sources."""
        return ArmAStore._support(sorted(support_rows, key=lambda x: (x["valid_from"], x["eu_id"])))

    def _why_surfaced(self, mediator, ta, tb, support_rows, agg, legs, names):
        """ADDED: X13 explanation payload: route, EUs, novelty, maturity, reason."""
        m_name = names.get(mediator, mediator)
        first_t = min(r["valid_from"] for r in support_rows)
        interest_link = self.conn.execute(
            "SELECT 1 FROM concept_interest_links WHERE concept_id=? LIMIT 1",
            (self.cid_of(mediator),)).fetchone()
        lifecycle_row = self.conn.execute(
            "SELECT user_relationship FROM concepts WHERE concept_id=?",
            (self.cid_of(mediator),)).fetchone()
        novel = interest_link is None and lifecycle_row["user_relationship"] == "unknown"
        channels = {}
        for r in support_rows:
            sid = r["source_id"]
            if sid not in channels:
                channels[sid] = self._source_channels.get(
                    sid,
                    self.conn.execute("SELECT channel FROM ea_evidence_units WHERE eu_id=?",
                                      (r["eu_id"],)).fetchone()["channel"])
        sources_detail = [
            {"source_id": sid, "channel": channels[sid]} for sid in agg["sources"]]
        return {
            "discovery_route": "; ".join(legs) + f" via {m_name}",
            "supporting_eus": agg["lineage_eus"],
            "novelty_state": (
                f"'{m_name}' is a NOVEL mediator: not a predeclared Interest"
                f" (user_relationship='unknown', no interest links); entered knowledge"
                f" only via evidence first seen {first_t}") if novel else
                f"'{m_name}' was already known/tracked.",
            "evidence_maturity": {
                "source_count": len(agg["sources"]),
                "independent_source_threshold": 2,
                "sources": sources_detail,
                "timestamps": sorted({r["valid_from"] for r in support_rows}),
            },
            "bridge_reason": (
                f"shared mediator '{m_name}' connects '{names.get(ta, ta)}' and"
                f" '{names.get(tb, tb)}' through evidence from >=2 independent"
                f" sources; the node itself was never user-declared."),
        }

    # -- evidence removal --------------------------------------------------

    def remove_eu(self, eu_id, reason="case"):
        """ADDED: tombstone removal; downstream downgrade of affected claims only."""
        row = self.conn.execute(
            "SELECT removed FROM ea_evidence_units WHERE eu_id=?", (eu_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown evidence unit: {eu_id}")
        if row["removed"]:
            return {"already_removed": True}
        affected = self.conn.execute(
            "SELECT subject,predicate,object FROM ea_assertions WHERE eu_id=?",
            (eu_id,)).fetchall()
        deactivated = self.conn.execute(
            "SELECT COUNT(*) AS c FROM ea_assertions WHERE eu_id=?",
            (eu_id,)).fetchone()["c"]
        self.conn.execute("UPDATE ea_evidence_units SET removed=1 WHERE eu_id=?", (eu_id,))
        self.conn.execute(
            "INSERT OR REPLACE INTO ea_removals (eu_id, removed_at, reason)"
            " VALUES (?, datetime('now'), ?)", (eu_id, reason))
        touched = {(a["subject"], a["predicate"], a["object"]) for a in affected}
        recomputed = 0
        for (s, p, o) in touched:  # keep current-relation projection consistent
            prov = self.provenance(s, p, o)
            live_pairs = [r for r in self._chain_rows(subject=s, predicate=p)]
            has_live_window = any(self._window_visible(r, None) and r["object"] == o
                                  for r in live_pairs)
            if not has_live_window:
                self.conn.execute(
                    "DELETE FROM concept_relations WHERE src_concept_id=? AND"
                    " dst_concept_id=? AND relation=?", (self.cid_of(s), self.cid_of(o), p))
            elif prov["eu_ids"]:
                self.conn.execute(
                    "UPDATE concept_relations SET evidence_json=? WHERE src_concept_id=?"
                    " AND dst_concept_id=? AND relation=?",
                    (json.dumps({"eu_ids": prov["eu_ids"]}), self.cid_of(s), self.cid_of(o), p))
            recomputed += 1
        self.conn.execute(
            "UPDATE ea_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT)"
            " WHERE key='generation'")
        self.conn.commit()
        return {"already_removed": False, "deactivated_assertions": deactivated,
                "affected_triples": sorted("|".join(t) for t in touched),
                "recomputed_relations": recomputed}

    # -- replay ------------------------------------------------------------

    def replay(self, checkpoint_ts):
        """ADDED: deterministic rebuild applying only EUs with t <= checkpoint.

        Reuses full ingest path (idempotent deterministic ids guarantee F-replay-free
        behavior); runs against an isolated memory store, main DB untouched.
        """
        if self._fixture is None:
            raise RuntimeError("replay requires ingest_fixture() first")
        cp = _t(checkpoint_ts)
        replayed = ArmAStore(None)
        replayed.load_entities(self._fixture["entities"])
        replayed._fixture = self._fixture
        subs = [u for u in sorted(self._fixture["evidence_units"],
                                  key=lambda u: _t(u["t"])) if _t(u["t"]) <= cp]
        for eu in subs:
            replayed.apply_eu(eu)
        return replayed, {"checkpoint": cp, "units_applied": len(subs)}
