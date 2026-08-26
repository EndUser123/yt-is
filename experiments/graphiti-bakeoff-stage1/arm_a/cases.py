"""X1..X14 case evaluation harness for Arm A.

HARNESS code (not counted as semantic machinery): each case calls the ArmAStore
query API, compares against the FROZEN expected table from PREREGISTRATION.md,
and never mutates the main store except through isolated clones/replays.
Failure classes come from the prereg: F-leak, F-prov, F-collapse, F-supersede,
F-identity, F-bridge, F-removal, F-replay, F-conc.
"""

from __future__ import annotations

from store import StaleWriteError

FAILURE_CLASS = {
    "X1": "F-leak", "X2": "F-prov", "X3": "F-supersede", "X4": "F-collapse",
    "X5": "F-identity", "X6": "F-bridge", "X7": "F-removal", "X8": "F-removal",
    "X9": "F-prov", "X10": "F-leak", "X11": "F-replay", "X12": "F-prov",
    "X13": "F-bridge", "X14": "F-conc",
}

T = {
    "2026-01-15": "2026-01-15T00:00:00Z",
    "2026-01-19": "2026-01-19T00:00:00Z",
    "2026-01-25": "2026-01-25T00:00:00Z",
    "2026-01-26": "2026-01-26T00:00:00Z",
    "2026-02-02": "2026-02-02T00:00:00Z",
}

EU16 = {"eu_id": "EU16", "t": "2026-02-05T00:00:00Z", "source_id": "S2",
        "text": "Follow-up confirms Dr. Mira Chen continues as lead of Project Alphard.",
        "asserts": [{"subject": "P1", "predicate": "leads", "object": "E1"}]}
EU17 = {"eu_id": "EU17", "t": "2026-02-06T00:00:00Z", "source_id": "S3",
        "text": "Rumour: Alphard budget revised to 9 million dollars.",
        "asserts": [{"subject": "E1", "predicate": "budget", "object": "9M"}]}


def _one(entries):
    return entries[0] if len(entries) == 1 else None


def case_x1(store):
    e = _one(store.as_of_query(subject="E2", predicate="launch_year",
                               as_of=T["2026-01-15"]))
    if e is None:
        return False, {"error": "expected exactly one claim entry"}
    cited = set(e["lineage_eus"]) | {u for v in e["current_values"] for u in v["backed_by"]}
    t_of = dict(store._fixture_eu_t())
    leak_free = all(t_of.get(u, "") <= T["2026-01-15"] for u in cited)
    ok = (e["status"] == "ASSERTED_ONLY"
          and [v["value"] for v in e["current_values"]] == ["2031"]
          and e["lineage_eus"] == ["EU03"] and leak_free)
    return ok, {"value": e["current_values"][0]["value"],
                "status": e["status"],
                "single_source_EU03": e["lineage_eus"] == ["EU03"],
                "leak_free": leak_free}


def case_x2(store):
    e = _one(store.as_of_query(subject="E1", predicate="partners_with"))
    ok = (e is not None and e["status"] == "SUPPORTED"
          and e["emergence"] == "2026-01-18T00:00:00Z"
          and sorted(e["lineage_eus"]) == ["EU06", "EU08"])
    return ok, {"status": e["status"] if e else None,
                "emergence": e["emergence"] if e else None,
                "eu_ids": sorted(e["lineage_eus"]) if e else [],
                "sources": e["sources"] if e else []}


def case_x3(store):
    now = _one(store.as_of_query(subject="E2", predicate="launch_year"))
    hist = _one(store.as_of_query(subject="E2", predicate="launch_year",
                                  as_of=T["2026-01-19"]))
    ok_now = (now is not None
              and [v["value"] for v in now["current_values"]] == ["2033"]
              and now["status"] == "SUPPORTED"
              and sorted(now["lineage_eus"]) == ["EU03", "EU09"])
    ok_hist = (hist is not None
               and [v["value"] for v in hist["current_values"]] == ["2031"]
               and hist["status"] == "ASSERTED_ONLY")
    return ok_now and ok_hist, {
        "now_value": now["current_values"][0]["value"] if now else None,
        "now_status": now["status"] if now else None,
        "now_lineage": sorted(now["lineage_eus"]) if now else [],
        "asof_0119_value": hist["current_values"][0]["value"] if hist else None,
        "asof_0119_status": hist["status"] if hist else None}


def case_x4(store):
    entries = store.as_of_query(subject="E1", predicate="budget")
    values = sorted(v["value"] for e in entries for v in e["current_values"])
    statuses = {e["status"] for e in entries}
    keys = {e["claim_key"] for e in entries}
    sup_hist = store.supersession_history("E1", "budget")
    no_supersession = len(sup_hist) == 0
    ok = (len(entries) == 2 and values == ["2M", "5M"]
          and statuses == {"ASSERTED_ONLY"} and len(keys) == 2 and no_supersession)
    return ok, {"values": values, "statuses": sorted(statuses),
                "distinct_claims": len(keys), "supersession_links": no_supersession}


def case_x5(store):
    got = {alias: store.resolve_name(alias) for alias in
           ["the Alphard program", "ALPHARD initiative", "Alphard", "Alphard Minor"]}
    ok = (got["the Alphard program"] == "E1" and got["ALPHARD initiative"] == "E1"
          and got["Alphard"] == "E1" and got["Alphard Minor"] == "E3"
          and got["Alphard Minor"] != got["Alphard"])
    return ok, got


def case_x6(store):
    bridges = store.find_bridges("T1", "T2")
    b = bridges[0] if len(bridges) == 1 else None
    novelty_ok = bool(b) and "not a predeclared Interest" in b["why_surfaced"]["novelty_state"]
    ok = (b is not None and b["mediator"] == "B1"
          and b["status"] == "SUPPORTED"
          and b["emergence"] == "2026-01-24T00:00:00Z"
          and sorted(b["aggregate_sources"]) == ["S1", "S3"]
          and sorted(b["supporting_eus"]) == ["EU10", "EU11"]
          and novelty_ok)
    return ok, {
        "path": b["path"] if b else [],
        "mediator": b["mediator"] if b else None,
        "status": b["status"] if b else None,
        "emergence": b["emergence"] if b else None,
        "aggregate_sources": sorted(b["aggregate_sources"]) if b else [],
        "mediator_was_not_predeclared_interest": bool(novelty_ok),
        "supporting_eus": sorted(b["supporting_eus"]) if b else []}


def case_x7(store, fixture):
    clone, _meta = store.replay("9999-12-31T23:59:59Z")   # identical full clone
    before_partners = _one(clone.as_of_query(subject="E1", predicate="partners_with"))
    result = clone.remove_eu("EU08")
    after = _one(clone.as_of_query(subject="E1", predicate="partners_with"))
    researches = _one(clone.as_of_query(subject="E1", predicate="researches",
                                        object_="T1"))
    launch = _one(clone.as_of_query(subject="E2", predicate="launch_year"))
    budget_entries = clone.as_of_query(subject="E1", predicate="budget")
    premise_supported = before_partners is not None and before_partners["status"] == "SUPPORTED"
    ok = (premise_supported
          and after is not None and after["status"] == "ASSERTED_ONLY"
          and after["lineage_eus"] == ["EU06"]
          and researches is not None and researches["status"] == "SUPPORTED"
          and sorted(researches["lineage_eus"]) == ["EU01", "EU02"]
          and launch and launch["current_values"][0]["value"] == "2033"
          and len({e["claim_key"] for e in budget_entries}) == 2)
    return ok, {"partners_status_after_removal": after["status"] if after else None,
                "partners_lineage_after": after["lineage_eus"] if after else [],
                "researches_T1_still_supported":
                    researches["lineage_eus"] if researches else [],
                "launch_value_intact": launch["current_values"][0]["value"] if launch else None,
                "budget_claims_intact": len({e["claim_key"] for e in budget_entries}),
                "removal_summary": result}


def case_x8(store, fixture):
    clone, _meta = store.replay("9999-12-31T23:59:59Z")
    clone.remove_eu("EU11")
    bridges = clone.find_bridges("T1", "T2")
    enables_t1 = _one(clone.as_of_query(subject="B1", predicate="enables",
                                        object_="T1"))
    enables_t2 = _one(clone.as_of_query(subject="B1", predicate="enables",
                                        object_="T2"))
    ok = (bridges == [] and enables_t1 is not None
          and enables_t1["lineage_eus"] == ["EU10"]
          and (enables_t2 is None))
    return ok, {"bridges_after_removal": len(bridges),
                "B1_enables_T1_remains": enables_t1["lineage_eus"] if enables_t1 else [],
                "B1_enables_T2_gone": enables_t2 is None}


def case_x9(store):
    e = _one(store.as_of_query(subject="P1", predicate="leads"))
    ok = (e is not None and e["status"] == "SUPPORTED"
          and e["emergence"] == T["2026-02-02"]
          and sorted(e["lineage_eus"]) == ["EU12", "EU15"]
          and sorted(e["sources"]) == ["S2", "S3"])
    return ok, {"status": e["status"] if e else None,
                "emergence": e["emergence"] if e else None,
                "eu_ids": sorted(e["lineage_eus"]) if e else []}


def case_x10(store):
    entries = store.as_of_query(subject="P1", predicate="leads",
                                as_of=T["2026-01-26"])
    e = _one(entries)
    eu_ids = sorted(e["lineage_eus"]) if e else []
    t_of = dict(store._fixture_eu_t())
    leaked = [u for u in eu_ids if t_of.get(u, "") > T["2026-01-26"]]
    ok = (e is not None and e["status"] == "ASSERTED_ONLY"
          and eu_ids == ["EU12"] and not leaked
          and all(v["backed_by"] == ["EU12"] for v in e["current_values"]))
    return ok, {"status": e["status"] if e else None,
                "visible_eus": eu_ids,
                "leaked_post_T_evidence": leaked,
                "EU15_absent": "EU15" not in eu_ids}


def _leak_probe(rep_store, cp):
    """Every EU cited by any as-of answer must have t <= checkpoint."""
    t_of = dict(rep_store._fixture_eu_t())
    leaks = []
    for s in rep_store.predeclared_entity_ids():
        for entry in rep_store.as_of_query(subject=s, as_of=cp):
            cited = set(entry["lineage_eus"])
            for v in entry["current_values"]:
                cited.update(v["backed_by"])
            for u in cited:
                if u in t_of and t_of[u] > cp:
                    leaks.append(u)
    return sorted(set(leaks))


def case_x11(store):
    cps = [("2026-01-05T00:00:00Z"), ("2026-01-18T00:00:00Z"),
           ("2026-01-20T00:00:00Z"), ("2026-02-02T00:00:00Z")]
    checks, details = [], {}
    expectations = [
        ("researches_T1_supported", ("E1", "researches", "T1"),
         lambda r: r and r["status"] == "SUPPORTED"
         and r["emergence"] <= "2026-01-05T23:59" and sorted(r["lineage_eus"]) == ["EU01", "EU02"]),
        ("partners_emerged", ("E1", "partners_with", None),
         lambda r: r and r["status"] == "SUPPORTED" and r["emergence"] == "2026-01-18T00:00:00Z"),
        ("launch_flipped_to_2033", ("E2", "launch_year", None),
         lambda r: r and [v["value"] for v in r["current_values"]] == ["2033"]),
        ("leads_supported", ("P1", "leads", None),
         lambda r: r and r["status"] == "SUPPORTED"
         and r["emergence"] == "2026-02-02T00:00:00Z"),
    ]
    for cp, (label, (s, p, o), pred_fn) in zip(cps, expectations):
        rep, meta = store.replay(cp)
        rows = rep.as_of_query(subject=s, predicate=p, object_=o)
        row = _one(rows)
        passed = pred_fn(row)
        leaks = _leak_probe(rep, cp)
        checks.append(bool(passed and not leaks))
        details[label] = {"checkpoint": cp, "pass": bool(passed), "leaks": leaks,
                          "actual": None if row is None else
                          {"status": row["status"], "emergence": row["emergence"],
                           "lineage_eus": row["lineage_eus"],
                           "current_values": row["current_values"]}}
    return all(checks) and len(checks) == 4, details


def case_x12(store):
    p = store.provenance("E1", "partners_with", object_="O1")
    r = store.provenance("E1", "researches", object_="T1")
    ok = (set(p["eu_ids"]) == {"EU06", "EU08"} and len(p["eu_ids"]) == 2
          and set(r["eu_ids"]) == {"EU01", "EU02"} and len(r["eu_ids"]) == 2)
    return ok, {"partners_with_O1": sorted(p["eu_ids"]),
                "researches_T1": sorted(r["eu_ids"])}


def case_x13(store):
    b = store.find_bridges("T1", "T2")[0]
    why = b["why_surfaced"]
    ok = (bool(why.get("discovery_route"))
          and set(why.get("supporting_eus", [])) >= {"EU10", "EU11"}
          and "not a predeclared Interest" in why.get("novelty_state", "")
          and why.get("evidence_maturity", {}).get("source_count") == 2
          and len(why.get("evidence_maturity", {}).get("timestamps", [])) >= 2
          and bool(why.get("bridge_reason")))
    return ok, why


def case_x14(store, fixture):
    """Sequential optimistic-concurrency simulation over three connections.

    Permitted by the task spec; the stale write genuinely executes its SQL-level
    generation guard and fails on rowcount==0 (no scripted errors).
    """
    sim = store.replay("9999-12-31T23:59:59Z")[0]
    n_reader = sim.generation                       # A holds generation N
    sim.apply_eu(EU16)                              # B commits new evidence -> N+1
    # C's as-of replay after B's commit: history at 02-02 is unchanged and the
    # concurrent EU16 (t=02-05) must not leak into it.
    c_view = sim.as_of_query(subject="P1", predicate="leads",
                             as_of=T["2026-02-02"])
    c_entry = _one(c_view)
    c_ok = (c_entry is not None and c_entry["status"] == "SUPPORTED"
            and sorted(c_entry["lineage_eus"]) == ["EU12", "EU15"]
            and all(set(v["backed_by"]) <= {"EU12", "EU15"}
                    for v in c_entry["current_values"]))
    c_no_leak = ("EU16" not in c_entry["lineage_eus"]
                 if c_entry else False)
    stale_failed, error = False, None
    try:
        sim.guarded_apply_eu(EU17, expected_generation=n_reader)
    except Exception as exc:                        # noqa: BLE001 - recorded behavior
        stale_failed = isinstance(exc, StaleWriteError)
        error = f"{type(exc).__name__}: {exc}"
    gen_final = sim.generation
    eu17_absent = sim.conn.execute(
        "SELECT COUNT(*) AS c FROM ea_evidence_units WHERE eu_id='EU17'").fetchone()["c"] == 0
    ok = (stale_failed and c_ok and c_no_leak
          and gen_final == n_reader + 1 and eu17_absent)
    return ok, {"reader_generation_N": n_reader,
                "post_commit_generation": gen_final,
                "concurrent_asof_replay_ok": c_ok,
                "asof_0202_no_leak_of_EU16": c_no_leak,
                "stale_write_outcome": "failed_with_StaleWriteError" if stale_failed
                                       else ("wrong_error" if error else "unexpectedly_succeeded"),
                "error_detail": error,
                "store_unchanged_by_stale_write": eu17_absent}
