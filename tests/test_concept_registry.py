"""Tests for ef.concept_registry: durable identity, mutable attention,
nothing deleted on decay, idempotent observations/episodes/relations,
read-only discovery radar."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from ef import concept_registry as cr


@pytest.fixture()
def conn(tmp_path):
    c = cr.connect(db_path=tmp_path / "registry.sqlite")
    yield c
    c.close()


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


REGISTRY_TABLES = {
    "concepts",
    "concept_aliases",
    "concept_observations",
    "trend_episodes",
    "concept_relations",
    "concept_interest_links",
    "concept_state_events",
    "discovery_runs",
}


def test_schema_idempotent_all_tables(tmp_path):
    db = tmp_path / "reg.sqlite"
    c = cr.connect(db_path=db)
    cr.ensure_schema(c)
    cr.ensure_schema(c)
    assert REGISTRY_TABLES <= _table_names(c)
    c.close()


def test_identity_id_deterministic():
    a = cr.concept_identity_id("technology", "NebulaMesh Runtime")
    b = cr.concept_identity_id("Technology", "nebulamesh  runtime")
    assert a == b and a.startswith("concept_")
    assert cr.concept_identity_id("tool", "NebulaMesh Runtime") != a
    assert cr.concept_identity_id("technology", "NebulaMesh Runtim") != a


def test_alias_normalization_and_resolution(conn):
    cid = cr.upsert_concept(conn, "Deep Foo Harness", "tool")
    cr.add_alias(conn, cid, "Deep Foo Harness")
    cr.add_alias(conn, cid, "deep-foo  HARNESS")  # same normalized form: ignored, still resolves
    assert cr.resolve_alias(conn, "deep foo harness") == cid
    assert cr.normalize_alias("Deep Foo Harness") == cr.normalize_alias("deep-foo  HARNESS")

    other = cr.upsert_concept(conn, "AI Agent Framework", "framework")
    cr.add_alias(conn, other, "AI Agent Framework")
    cr.add_alias(conn, cid, "Agent")
    # token overlap must never merge: "Agent" does not resolve to the framework
    assert cr.resolve_alias(conn, "Agent") == cid
    assert cr.resolve_alias(conn, "AI Agent Frameworks") is None


def test_observation_idempotent(conn):
    cid = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime")
    oid1 = cr.record_observation(
        conn, cid, source_kind="video", source_id="v1", observed_at="2026-08-20", title="t1"
    )
    oid2 = cr.record_observation(
        conn, cid, source_kind="video", source_id="v1", observed_at="2026-08-20", title="t1"
    )
    assert oid1 == oid2
    assert conn.execute("SELECT COUNT(*) c FROM concept_observations").fetchone()["c"] == 1
    row = cr.get_concept(conn, cid)
    assert row["evidence_count"] == 1 and row["last_seen"] == "2026-08-20"
    cr.record_observation(
        conn, cid, source_kind="post", source_id="p1", observed_at="2026-08-22"
    )
    row = cr.get_concept(conn, cid)
    assert row["evidence_count"] == 2
    assert row["source_diversity"] == 2
    assert row["last_seen"] == "2026-08-22"
    # last_seen only moves forward
    cr.record_observation(
        conn, cid, source_kind="post", source_id="p0", observed_at="2026-01-01"
    )
    assert cr.get_concept(conn, cid)["last_seen"] == "2026-08-22"
    assert cr.observation_counts(conn, cid) == {"total": 3, "distinct_source_kinds": 2}


def test_lifecycle_events_and_durability(conn):
    cid = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime", first_seen="2026-01-01")
    first = cr.get_concept(conn, cid)
    for state in ("emerging", "cooling", "dormant"):
        cr.set_lifecycle(conn, cid, state, reason=f"moved to {state}")
    row = cr.get_concept(conn, cid)
    assert row["lifecycle_state"] == "dormant"
    assert row["first_seen"] == first["first_seen"] == "2026-01-01"
    assert row["discovered_at"] == first["discovered_at"]
    events = conn.execute(
        "SELECT * FROM concept_state_events WHERE concept_id = ? ORDER BY rowid", (cid,)
    ).fetchall()
    assert [e["new_value"] for e in events] == ["emerging", "cooling", "dormant"]
    assert all(e["field"] == "lifecycle_state" for e in events)
    # no-op when same state
    cr.set_lifecycle(conn, cid, "dormant", reason="again")
    assert conn.execute("SELECT COUNT(*) c FROM concept_state_events").fetchone()["c"] == 3


def test_invalid_values_raise(conn):
    cid = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime")
    with pytest.raises(cr.RegistryError):
        cr.set_lifecycle(conn, cid, "exploded", reason="bad")
    with pytest.raises(cr.RegistryError):
        cr.set_user_relationship(conn, cid, "best_friend", reason="bad", method="operator")
    with pytest.raises(cr.RegistryError):
        cr.upsert_concept(conn, "X", "t", lifecycle_state="nope")
    with pytest.raises(cr.RegistryError):
        cr.upsert_concept(conn, "X", "t", user_relationship="nope")


def test_user_relationship_authority(conn):
    cid = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime")
    cr.set_user_relationship(conn, cid, "adjacent", reason="cluster", method="shared_cluster")
    with pytest.raises(cr.RegistryError):
        cr.set_user_relationship(conn, cid, "durable_interest", reason="llm says so", method="llm")
    cr.set_user_relationship(conn, cid, "durable_interest", reason="operator", method="operator")
    with pytest.raises(cr.RegistryError):
        cr.set_user_relationship(
            conn, cid, "rejected", reason="semantic", method="semantic"
        )
    cr.set_user_relationship(conn, cid, "monitoring", reason="watch", method="semantic")


def test_trend_episodes(conn):
    cid = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime")
    ep1 = cr.open_trend_episode(
        conn, cid, started_at="2026-08-01", baseline_rate=0.1, policy_version="pv1"
    )
    cr.update_trend_episode(
        conn, ep1, recent_rate=0.5, acceleration=2.0, novelty_score=0.8,
        last_active_at="2026-08-05", peak_at="2026-08-04",
    )
    cr.close_trend_episode(conn, ep1, ended_at="2026-08-10", state="cooled")
    assert cr.active_episode(conn, cid) is None
    ep2 = cr.open_trend_episode(
        conn, cid, started_at="2026-08-15", baseline_rate=0.1, policy_version="pv1"
    )
    assert ep2 != ep1
    assert cr.active_episode(conn, cid)["episode_id"] == ep2
    snap = conn.execute(
        "SELECT * FROM trend_episodes WHERE episode_id = ?", (ep1,)
    ).fetchone()
    assert snap["state"] == "cooled" and snap["ended_at"] == "2026-08-10"
    assert snap["recent_rate"] == 0.5 and snap["acceleration"] == 2.0
    assert snap["novelty_score"] == 0.8 and snap["peak_at"] == "2026-08-04"
    # re-open replay is idempotent
    assert cr.open_trend_episode(
        conn, cid, started_at="2026-08-15", baseline_rate=0.1, policy_version="pv1"
    ) == ep2


def test_merge_concepts(conn):
    survivor = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime")
    dupe = cr.upsert_concept(conn, "Nebula Mesh Runtime", "runtime")
    cr.add_alias(conn, dupe, "nebulamesh")
    cr.record_observation(
        conn, dupe, source_kind="video", source_id="v9", observed_at="2026-08-20"
    )
    cr.merge_concepts(conn, survivor, dupe, run_id="run1")
    assert cr.get_concept(conn, dupe)["lifecycle_state"] == "obsolete"
    assert cr.resolve_alias(conn, "nebulamesh") == survivor
    moved = conn.execute(
        "SELECT COUNT(*) c FROM concept_observations WHERE concept_id = ?", (survivor,)
    ).fetchone()["c"]
    assert moved == 1
    evt = conn.execute(
        "SELECT * FROM concept_state_events WHERE concept_id = ? AND field = 'lifecycle_state'"
        " ORDER BY rowid DESC LIMIT 1",
        (dupe,),
    ).fetchone()
    assert evt["new_value"] == "obsolete"
    with pytest.raises(cr.RegistryError):
        cr.merge_concepts(conn, survivor, survivor)


def test_relation_replay_no_duplicate(conn):
    a = cr.upsert_concept(conn, "A Concept", "t")
    b = cr.upsert_concept(conn, "B Concept", "t")
    cr.record_concept_relation(conn, a, b, "relates_to", 0.7, "llm")
    cr.record_concept_relation(conn, a, b, "relates_to", 0.9, "llm")
    rows = conn.execute("SELECT * FROM concept_relations").fetchall()
    assert len(rows) == 1 and rows[0]["confidence"] == 0.9


def test_discovery_radar_readonly_and_ranking(conn):
    emerging = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime",
                                 world_signal_score=0.9, metadata={"novelty_flags": ["new-word"]})
    cr.set_lifecycle(conn, emerging, "emerging", reason="spike detected")
    cr.record_observation(
        conn, emerging, source_kind="video", source_id="v1", observed_at="2026-08-20",
        metadata={"discovery_method": "semantic"},
    )
    cr.link_concept_interest(conn, emerging, "int-1", method="semantic")
    candidate = cr.upsert_concept(conn, "Quiet Thing", "tool", world_signal_score=0.1)
    dormant = cr.upsert_concept(conn, "Old Thing", "tool", world_signal_score=0.95)
    cr.set_lifecycle(conn, dormant, "dormant", reason="decayed")
    cr.open_trend_episode(conn, emerging, started_at="2026-08-20",
                          baseline_rate=0.1, policy_version="pv1")
    ep = cr.active_episode(conn, emerging)["episode_id"]
    cr.update_trend_episode(conn, ep, acceleration=1.5, novelty_score=0.7)

    def snapshot():
        return {
            t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            for t in REGISTRY_TABLES
        }

    before = snapshot()
    radar = cr.discovery_radar(conn)
    assert snapshot() == before
    assert [r["concept_id"] for r in radar] == [emerging, candidate, dormant]
    top = radar[0]
    for key in (
        "concept_id", "name", "type", "lifecycle", "user_relationship",
        "first_seen", "last_seen", "world_signal_score", "personal_relevance_score",
        "acceleration", "novelty_score", "source_diversity", "why_surfaced",
        "related_interests",
    ):
        assert key in top
    assert top["acceleration"] == 1.5 and top["novelty_score"] == 0.7
    assert top["related_interests"] == ["int-1"]
    assert "spike detected" in top["why_surfaced"] and "semantic" in top["why_surfaced"]
    assert top["novelty_flags"] == ["new-word"]
    assert radar[2]["acceleration"] is None and radar[2]["novelty_score"] is None


def test_upsert_concept_idempotent_updates_scores(conn):
    c1 = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime", world_signal_score=0.2)
    c2 = cr.upsert_concept(conn, "NebulaMesh Runtime", "runtime", world_signal_score=0.8,
                           personal_relevance_score=0.4)
    assert c1 == c2
    assert conn.execute("SELECT COUNT(*) c FROM concepts").fetchone()["c"] == 1
    row = cr.get_concept(conn, c1)
    assert row["world_signal_score"] == 0.8
    assert row["personal_relevance_score"] == 0.4
    assert cr.list_concepts(conn, lifecycle="candidate")[0]["concept_id"] == c1
