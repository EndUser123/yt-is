"""Transition semantics of the alert ledger (scripts/alert_ledger.py).

The ledger replaces rewrite-every-tick alerting as the system of record:
new conditions open, persisting conditions update state silently (no
transition row — that is the point), vanished conditions resolve.
"""

from __future__ import annotations

import json

from scripts import alert_ledger


def test_open_persist_resolve_cycle(tmp_path):
    d = tmp_path / "alerts"
    t1 = alert_ledger.record(
        ["[tasks] nightly task failure: YtisContentSync=1"],
        d, now="2026-08-24T00:00:00+00:00",
    )
    assert [t["event"] for t in t1] == ["opened"]
    assert t1[0]["event_id"] and t1[0]["dedupe_key"]

    # Same condition again: state updates, but NO new transition row —
    # chronic conditions must not spam the ledger.
    t2 = alert_ledger.record(
        ["[tasks] nightly task failure: YtisContentSync=1"],
        d, now="2026-08-24T00:05:00+00:00",
    )
    assert t2 == []

    state = json.loads((d / "open.json").read_text(encoding="utf-8"))
    (event,) = state["events"].values()
    assert event["count"] == 2
    assert event["first_seen"] == "2026-08-24T00:00:00+00:00"
    assert event["last_seen"] == "2026-08-24T00:05:00+00:00"

    # Healthy tick: everything resolves, exactly one resolution row.
    t3 = alert_ledger.record([], d, now="2026-08-24T00:10:00+00:00")
    assert [t["event"] for t in t3] == ["resolved"]
    assert t3[0]["event_id"] == t1[0]["event_id"]

    final = json.loads((d / "open.json").read_text(encoding="utf-8"))
    assert final["events"] == {}


def test_digit_drift_does_not_flap_events(tmp_path):
    d = tmp_path / "alerts"
    alert_ledger.record(
        ["[health] state=STOPPED_FAILURE: 3412 rows pending"],
        d, now="2026-08-24T00:00:00+00:00",
    )
    alert_ledger.record(
        ["[health] state=STOPPED_FAILURE: 3413 rows pending"],
        d, now="2026-08-24T00:05:00+00:00",
    )
    state = json.loads((d / "open.json").read_text(encoding="utf-8"))
    assert len(state["events"]) == 1  # count drift is an update, not a new event


def test_ledger_jsonl_rows_are_valid_and_ordered(tmp_path):
    d = tmp_path / "alerts"
    alert_ledger.record(["[tasks] x=1"], d, now="2026-08-24T00:00:00+00:00")
    alert_ledger.record([], d, now="2026-08-24T00:05:00+00:00")
    rows = [
        json.loads(line)
        for line in (d / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [r["event"] for r in rows] == ["opened", "resolved"]
    assert rows[0]["event_id"] == rows[1]["event_id"]


def test_severity_from_warning_tag(tmp_path):
    d = tmp_path / "alerts"
    alert_ledger.record(["[tasks-warning] probe degraded"], d)
    state = json.loads((d / "open.json").read_text(encoding="utf-8"))
    (event,) = state["events"].values()
    assert event["severity"] == "warning"
