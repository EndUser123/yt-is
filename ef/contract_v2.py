"""CONTRACT ARCHITECTURE v2 — decomposed object inference vs relations.

Implements the 2026-08-27 architect packet:

  PHASE 1   provider emits independent semantic objects (interests WITHOUT
            parent/related_to; questions WITHOUT an interest reference;
            regret candidates WITHOUT related_interests). Each object is
            independently validatable; one invalid optional object never
            destroys otherwise-valid objects.
  IDENTIFY  deterministic local IDs are assigned MECHANICALLY after
            validation (reusing the fragment-ID recipe: order-insensitive,
            run-scoped, auditable). The LLM never invents IDs.
  RELATE    a separate constrained call receives the validated inventory
            (IDs + names) and returns ONLY relationships between those IDs.
            Endpoints are verified mechanically; invalid OPTIONAL edges are
            quarantined with receipts; cycles are resolved deterministically;
            a relation-stage defect cannot corrupt the already-validated
            object set.
  GROUP     decomposed reconciliation: the provider proposes
            equivalence groups over validated source-object IDs; mechanical
            code owns exhaustive accounting (zero silent loss), provenance
            union, disposition receipts, and final assembly. Assembly maps
            everything back into the EXACT v1 payload shape and hands it to
            the strict existing validate_inference — no relaxation.

Pure logic plus text rendering: no DB/network/subprocess here. Provider
execution stays in scripts/* drivers that import this module.
"""

from __future__ import annotations

import hashlib
import json

from ef.inference_contract import conformance_errors, \
    phase1_output_schema

SEMANTIC_FIELDS_ORDER = (
    "name", "kind", "temporal_state", "stance", "confidence",
    "observed_vs_inferred", "goal", "information_need", "cluster_ids",
    "evidence_summary", "counterevidence",
)

PHASE1_CONTRACT_BLOCK = """Return ONLY valid JSON matching this schema:
{{
  "inferred_interests": [
    {{
      {semantic_fields}
    }}
  ],
  "questions": [
    {{
      "text": "string — an open question the person appears to be investigating",
      "status": "open|watching"
    }}
  ],
  "regret_candidates": [
    {{
      "topic": "string — adjacent topic poorly represented but strongly implied",
      "why": "string — why this matters given demonstrated goals",
      "label": "inferred_adjacent",
      "confidence": 0.0-1.0,
      "cluster_ids": [int]
    }}
  ]
}}

Hard constraints:
- This is PHASE 1 ONLY: you infer independent semantic OBJECTS.
- Do NOT emit parent, hierarchy, or any cross-object relationship here —
  relationships are inferred by a separate later stage.
- cluster_ids must reference ONLY cluster ids supplied above.
- Every interest needs a non-empty evidence_summary and at least one
  cluster_id. confidence is a number between 0 and 1 (not true/false).
"""


def render_phase1_prompt(semantic_prose: str, n_clusters: int,
                         packets: str) -> str:
    """Assemble the phase-1 prompt.

    ``semantic_prose`` is the verbatim semantic guidance paragraph block
    from the frozen v2 template (everything up to the 'Return ONLY' line);
    callers pin its byte-equality with a drift-guard test so semantic
    definitions cannot silently diverge.
    """
    fields = "\n".join(
        f'"{f}": "{_FIELD_DOC[f]}"' if f in _FIELD_DOC else f'"{f}": ...'
        for f in SEMANTIC_FIELDS_ORDER)
    body = PHASE1_CONTRACT_BLOCK.format(semantic_fields=fields)
    return (f"{semantic_prose}\n{body}\nEVIDENCE CLUSTERS:\n\n{packets}")


_FIELD_DOC = {
    "name": "string — canonical name",
    "kind": "domain|topic|subtopic|method|monitor",
    "temporal_state":
        "durable|active|current_problem|episodic|emerging|dormant",
    "stance": "curiosity|learning|project|monitoring|entertainment",
    "confidence": "0.0-1.0",
    "observed_vs_inferred": "observed|inferred|inferred_adjacent",
    "goal": "string — the underlying goal/problem, or null",
    "information_need":
        "string — what they are repeatedly trying to learn, or null",
    "cluster_ids": "[int]",
    "evidence_summary": "string — what evidence supports this",
    "counterevidence": "string — what argues against this, or null",
}

RELATION_PROMPT_TEMPLATE = """You are given VALIDATED interest objects from a personal-corpus analysis, each already carrying a mechanical stable id. Invent NOTHING: your job is only to propose RELATIONSHIPS between these existing objects.

OBJECT INVENTORY (JSON):

{inventory}

Propose, as pure references between the ids above:
- parent_edges: child_id sits under parent_id when one object is clearly a specific facet of another broader object. No cycles; an object is never its own parent.
- related_edges: source_id/target_id pairs of genuinely related interests.
- question_links: each question belongs under exactly one interest id when identifiable from wording alone.
- regret_links: each regret candidate points at an adjacent listed interest when identifiable.
Omit an edge rather than guessing. Do NOT rewrite, merge, rename, score, or add objects.

Return ONLY valid JSON:"""


def render_relation_prompt(inventory) -> str:
    rows = [{"id": o["id"], "type": o["type"],
             **({k: o[k] for k in ("name",)} if o["type"] == "interest"
                else {k: o[k] for k in ("text",)}
                if o["type"] == "question" else
                {k: o[k] for k in ("topic",)})}
            for o in inventory]
    return RELATION_PROMPT_TEMPLATE.format(
        inventory=json.dumps(rows, indent=1, ensure_ascii=False))


def make_object_id(kind: str, batch_id: str, name: str, cluster_ids) -> str:
    """Deterministic within-run object identity (fragment-ID recipe).

    Order-insensitive over cluster_ids; collides only when kind+batch+
    normalized name+identical evidence set collide, which validation has
    already deduplicated upstream.
    """
    payload = "\x1f".join((
        kind, batch_id, " ".join(str(name).strip().casefold().split()),
        ",".join(str(c) for c in sorted(cluster_ids))))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    prefix = {"interest": "int", "question": "qst",
              "regret": "rgt"}[kind]
    return f"{prefix}_{digest}"


# ---------------------------------------------------------------------------
# Phase-1 per-object validation
# ---------------------------------------------------------------------------

_SCHEMA_BY_ARRAY = {
    "inferred_interests": "#/$defs/interest",
    "questions": "#/$defs/question",
    "regret_candidates": "#/$defs/regret",
}


def validate_phase1_payload(payload, supplied_cluster_ids):
    """Object-wise phase-1 validation. NEVER raises on item defects.

    Returns (valid, failures).
      valid: {"inferred_interests":[..], "questions":[..],
              "regret_candidates":[..]} retaining only fully valid objects
      failures: [{array,index,severity:'core'|'optional',
                  error_class:'schema_enforcement'|'semantic',error}]
    Envelope-level problems (not a dict / missing arrays / empty interests)
    raise ValueError — the call itself is unusable.
    """
    supplied = set(supplied_cluster_ids)
    schema = phase1_output_schema()
    envelope_errs = [e for e in conformance_errors(payload, schema)
                     if ".inferred_interests[" not in e
                     and ".questions[" not in e
                     and ".regret_candidates[" not in e]
    if envelope_errs:
        raise ValueError(f"phase-1 envelope violation: "
                         f"{envelope_errs[:4]}")
    if not isinstance(payload.get("inferred_interests"), list) or \
            not payload["inferred_interests"]:
        raise ValueError("phase-1 emitted no interests at all")

    defs = schema["$defs"]
    ref_schemas = {
        arr: defs[_SCHEMA_BY_ARRAY[arr].split("/")[-1]]
        for arr in _SCHEMA_BY_ARRAY}
    valid: dict[str, list] = {k: [] for k in _SCHEMA_BY_ARRAY}
    failures: list[dict] = []
    seen_names: set[str] = set()

    def check_items(array: str, severity: str, semantic_fn=None):
        items = payload.get(array, [])
        for i, obj in enumerate(items):
            errs = conformance_errors(obj, ref_schemas[array])
            if errs:
                failures.append({
                    "array": array, "index": i, "severity": severity,
                    "error_class": "schema_enforcement",
                    "error": "; ".join(errs)[:300]})
                continue
            if semantic_fn:
                err = semantic_fn(i, obj)
                if err:
                    failures.append({
                        "array": array, "index": i, "severity": severity,
                        "error_class": "semantic", "error": err})
                    continue
            valid[array].append(obj)

    def interest_semantic(_i, obj):
        name = obj["name"].strip().casefold()
        if name in seen_names:
            return (f"duplicate interest name within batch: "
                    f"{obj['name']!r}")
        bad = [c for c in obj["cluster_ids"]
               if c not in supplied]
        if bad:
            return f"cluster ids not supplied in packet: {bad}"
        seen_names.add(name)
        return None

    check_items("inferred_interests", "core", interest_semantic)

    def text_semantic(_i, obj):
        field = "text"
        value = obj.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"{field} must be a non-empty string"

    def regret_semantic(_i, obj):
        value = obj.get("topic")
        if not isinstance(value, str) or not value.strip():
            return "topic must be a non-empty string"
        bad = [c for c in obj["cluster_ids"] if c not in supplied]
        if bad:
            return f"cluster ids not supplied in packet: {bad}"
        return None

    check_items("questions", "optional", text_semantic)
    check_items("regret_candidates", "optional", regret_semantic)
    return valid, failures


def build_inventory(valid: dict, batch_id: str) -> list[dict]:
    """Mechanically assign stable ids to every valid phase-1 object.

    Same-content duplicates within one batch (identical question text or
    regret topic) disambiguate deterministically via an occurrence suffix
    so their ids can never collide (review finding F4).
    """
    inventory: list[dict] = []
    occurrence: dict[str, int] = {}

    def uid(kind_label: str, name_key: str, cids) -> str:
        # normalize EXACTLY like make_object_id does, so case/whitespace
        # variants share an occurrence slot and can never collide ids
        norm = " ".join(str(name_key).strip().casefold().split())
        slot = kind_label + "|" + norm
        occ = occurrence.get(slot, 0)
        occurrence[slot] = occ + 1
        base = make_object_id(kind_label, batch_id, name_key, cids)
        return base if occ == 0 else f"{base}x{occ}"

    for it in valid["inferred_interests"]:
        oid = uid("interest", it["name"], it["cluster_ids"])
        inventory.append({"id": oid, "batch_id": batch_id,
                          "type": "interest", "name": it["name"],
                          "cluster_ids": list(it["cluster_ids"]),
                          "object": it})
    for q in valid["questions"]:
        oid = uid("question", q["text"], [])
        inventory.append({"id": oid, "batch_id": batch_id,
                          "type": "question", "text": q["text"],
                          "cluster_ids": [], "object": q})
    for rc in valid["regret_candidates"]:
        oid = uid("regret", rc["topic"], rc["cluster_ids"])
        inventory.append({"id": oid, "batch_id": batch_id,
                          "type": "regret", "topic": rc["topic"],
                          "cluster_ids": list(rc["cluster_ids"]),
                          "object": rc})
    return inventory


# ---------------------------------------------------------------------------
# Relation stage — mechanical endpoint verification + quarantine + DAG
# ---------------------------------------------------------------------------

def verify_relations(wrapper, interest_ids, question_ids, regret_ids):
    """Verify endpoints of a relation-call result against exact id sets.

    Returns (accepted, quarantine). accepted keys mirror the four edge
    vocabularies and contain ONLY edges whose endpoints exist and type.
    Quarantine receipts explain every dropped edge. Parent edges are
    additionally filtered into a DAG deterministically (provider order;
    an edge creating a cycle or pointing at itself is quarantined).
    Raises nothing; missing arrays / non-list entries count as
    enforcement failures recorded through quarantine receipts prefixed
    ENVELOPE (the caller decides arm policy on those).
    """
    by_type = {"interest": set(interest_ids),
               "question": set(question_ids),
               "regret": set(regret_ids)}
    accepted: dict[str, list] = {"parent_edges": [], "related_edges": [],
                                 "question_links": [], "regret_links": []}
    quarantine: list[dict] = []

    def exists(oid: str, want: str) -> bool:
        return oid in by_type[want]

    def edge_iter(key):
        """Yield (index, value-or-quarantine-marker) tolerating ANY
        malformed envelope: non-list arrays and non-dict elements are
        quarantined with receipts, never raised (raises-nothing contract)."""
        raw = wrapper.get(key)
        if raw is None:
            return
        if not isinstance(raw, list):
            quarantine.append({"edge": key, "index": None,
                               "edge_obj": raw if isinstance(
                                   raw, (str, int, float, bool)) else None,
                               "reason": "ENVELOPE: array is not a list"})
            return
        for i, e in enumerate(raw):
            if not isinstance(e, dict):
                quarantine.append({"edge": key, "index": i,
                                   "edge_obj": e,
                                   "reason": "ENVELOPE: element not object"})
                continue
            yield i, e

    # related: both endpoints must be interests
    for i, e in edge_iter("related_edges"):
        if not exists(e.get("source_id"), "interest") or \
                not exists(e.get("target_id"), "interest"):
            quarantine.append({"edge": "related_edges", "index": i,
                               "edge_obj": e,
                               "reason": "endpoint unknown/not-interest"})
            continue
        if e["source_id"] == e["target_id"]:
            quarantine.append({"edge": "related_edges", "index": i,
                               "edge_obj": e, "reason": "self-reference"})
            continue
        accepted["related_edges"].append(e)

    parents: dict[str, str] = {}
    for i, e in edge_iter("parent_edges"):
        child, parent = e.get("child_id"), e.get("parent_id")
        if not exists(child, "interest") or \
                not exists(parent, "interest"):
            quarantine.append({"edge": "parent_edges", "index": i,
                               "edge_obj": e,
                               "reason": "endpoint unknown/not-interest"})
            continue
        if child == parent:
            quarantine.append({"edge": "parent_edges", "index": i,
                               "edge_obj": e, "reason": "self-parent"})
            continue
        # walk up the tentative chain: would this create a cycle?
        node, cyclic = parent, False
        while node in parents:
            node = parents[node]
            if node == child:
                cyclic = True
                break
        if cyclic:
            quarantine.append({"edge": "parent_edges", "index": i,
                               "edge_obj": e,
                               "reason": "would create parent cycle"})
            continue
        parents[child] = parent
        accepted["parent_edges"].append(e)

    for key in ("question_links", "regret_links"):
        member_type = "question" if key == "question_links" else "regret"
        id_field, int_field = (
            ("question_id", "interest_id") if key == "question_links"
            else ("regret_id", "interest_id"))
        for i, e in edge_iter(key):
            if not exists(e.get(id_field), member_type) or \
                    not exists(e.get(int_field), "interest"):
                quarantine.append({"edge": key, "index": i,
                                   "edge_obj": e,
                                   "reason": "endpoint unknown/wrong type"})
                continue
            accepted[key].append(e)

    return accepted, quarantine


# ---------------------------------------------------------------------------
# Decomposed reconciliation — grouping + mechanical canonical assembly
# ---------------------------------------------------------------------------

def verify_group_coverage(groups, inventory):
    """Every input object id must appear in exactly one group.

    Returns (ok, covered_ids, problems[]). Group shape was schema-checked
    by the caller; this adds cross-group accounting.
    """
    input_ids = [o["id"] for o in inventory]
    covered: list[str] = []
    problems: list[str] = []
    group_arrays = [g.get("members") for g in groups
                    if isinstance(g, dict)]
    for gi, members in enumerate(group_arrays):
        if not isinstance(members, list) or not members:
            problems.append(f"group[{gi}]: members must be a non-empty list")
            continue
        covered.extend(members)
    dupes = sorted({m for m in covered if covered.count(m) > 1})
    if dupes:
        problems.append(f"objects assigned twice: {dupes[:5]}")
    unknown = sorted(set(covered) - set(input_ids))
    if unknown:
        problems.append(f"groups reference unknown ids: {unknown[:5]}")
    missing = sorted(set(input_ids) - set(covered))
    if missing:
        problems.append(f"objects without any group: {missing[:8]}"
                        + (f" (+{len(missing) - 8} more)"
                           if len(missing) > 8 else ""))
    return (not problems), covered, problems


def assemble_canonical(groups, inventory, eligible_cluster_ids):
    """Deterministic canonical builder — the mechanical half of recon v2.

    Semantics:
    - action=distinct      -> the single member becomes canonical.
    - action=merged        -> representative chosen deterministically
      (highest confidence, tie-break lexicographically smallest id);
      cluster provenance = union of ALL member cluster_ids; member ids
      and dispositions recorded. NO other field of the representative's
      object is altered: zero semantic rewriting.
    - action=drop_noise    -> members recorded as explicitly discarded
      with the provider reason (an explicit disposition; allowed for
      questions/regrets only — core interests may not be dropped, mirroring
      the frozen v1 rule that silently dropping all inferred state is a
      violation).
    Returns (canonical_objects, dispositions); raises ValueError when a
    group itself is malformed beyond repair (member id collisions were
    already excluded by verify_group_coverage).
    """
    by_id = {o["id"]: o for o in inventory}
    canon: list[dict] = []
    dispositions: list[dict] = []

    def disposition(oid, decision, target, reason):
        dispositions.append({"source_id": oid, "decision": decision,
                             "target_id": target, "reason": reason})

    for gi, g in enumerate(groups):
        members = [by_id[m] for m in g["members"] if m in by_id]
        action = g["action"]
        reason = g.get("reason") or ""
        if len(members) != len(g["members"]):
            raise ValueError(f"group[{gi}] contains ids outside inventory")
        if action == "drop_noise":
            if any(m["type"] == "interest" for m in members):
                raise ValueError(
                    f"group[{gi}]: core interests cannot be dropped; "
                    "only questions/regret candidates qualify")
            for m in members:
                disposition(m["id"], "discarded", None,
                            f"drop_noise: {reason}")
            continue

        def sort_key(o):
            conf = o["object"].get("confidence", 0.0)
            return (-float(conf), o["id"])
        ordered_members = sorted(members, key=sort_key)

        # ONE disposition per member (review finding F2: never N^2).
        if action == "merged":
            survivor_id = ordered_members[0]["id"]
            for m in members:
                disposition(m["id"],
                            "kept" if m["id"] == survivor_id else "merged",
                            survivor_id, reason)
        else:  # distinct
            for m in members:
                disposition(m["id"], "kept", m["id"], reason)

        # canonical entries: distinct -> one per member; merged -> one rep
        reps = ordered_members if action == "distinct" \
            else [ordered_members[0]]
        for rep_member in reps:
            label = (rep_member["object"].get("name")
                     or rep_member["object"].get("text")
                     or rep_member["object"].get("topic"))
            merged_names = ([m["object"].get("name")
                             or m["object"].get("text")
                             or m["object"].get("topic")
                             for m in members]
                            if action == "merged" else [label])
            canon.append({
                "canonical_id": rep_member["id"],
                "type": rep_member["type"],
                "object": json.loads(
                    json.dumps(rep_member["object"])),
                "source_ids": ([m["id"] for m in members]
                               if action == "merged"
                               else [rep_member["id"]]),
                "provenance_cluster_ids":
                    sorted({c for m in members
                            for c in m["cluster_ids"]})
                    if action == "merged"
                    else sorted(set(rep_member["cluster_ids"])),
                "dispositions_reason": reason,
                "_members_named": merged_names,
                "_group_index": gi,
            })
    # Rename-collision safety net (falsifier: duplicate semantic objects
    # across batches). Name/text/topic EQUALITY is mechanically checkable,
    # so identical keys are merged HERE regardless of provider grouping:
    # deterministic representative + provenance union + explicit receipts.
    # The provider remains responsible only for judgment-based equivalence.
    def _key(c):
        o = c["object"]
        return " ".join(str(o.get("name") or o.get("text")
                            or o.get("topic")).strip().casefold().split())

    def mech_merge(entries):
        def sort_key(c):
            return (-float(c["object"].get("confidence", 0.0)),
                    c["canonical_id"])
        ordered = sorted(entries, key=sort_key)
        rep = dict(ordered[0])
        rep["object"] = json.loads(json.dumps(rep["object"]))
        union_cids = sorted({cid for e in entries
                             for cid in e["provenance_cluster_ids"]})
        rep["provenance_cluster_ids"] = union_cids
        rep["source_ids"] = [sid for e in entries
                             for sid in e["source_ids"]]
        rep["_members_named"].append(
            f"mechanical-merge of {len(entries)} same-key objects")
        for e in entries[1:]:
            dispositions.append({
                "source_id": e["canonical_id"],
                "decision": "mechanically_merged",
                "target_id": rep["canonical_id"],
                "reason": "mechanical same-identity merge "
                          "(deterministic, provenance unioned)"})
        return rep

    deduped: list[dict] = []
    buckets: dict[tuple[str, str], list[dict]] = {}
    for c in canon:
        buckets.setdefault((c["type"], _key(c)), []).append(c)
    single_bucket_seen_multi = False
    for (_, _k), entries in sorted(buckets.items()):
        if len(entries) == 1:
            deduped.append(entries[0])
        else:
            single_bucket_seen_multi = True
            deduped.append(mech_merge(entries))
    canon = deduped

    # Final invariant preserved from v1: canonical interest names unique
    seen: set[str] = set()
    for c in canon:
        if c["type"] != "interest":
            continue
        nm = _key(c)
        if nm in seen:
            raise ValueError(
                "grouping produced unmergeable conflicting canonical "
                f"interests keyed {c['object'].get('name')!r}; provider "
                "groups violated the equivalence contract")
        seen.add(nm)
    return canon, dispositions


def apply_relations_to_assembly(canonical, accepted, dispositions_extra):
    """Attach accepted edges back onto canonical objects (v1 field shapes).

    Question links REQUIRE resolution: a canonical question left unlinked
    is recorded as a required-link failure receipt and EXCLUDED from the
    final payload (explicit, never fabricated). Regret links and optional
    interest edges attach where present; quarantined edges live solely in
    receipts.
    """
    interests = [c for c in canonical if c["type"] == "interest"]
    cid_by_oid = {c["canonical_id"]: c for c in canonical}
    name_by_oid = {c["canonical_id"]: c["object"]["name"]
                   for c in interests}
    for e in accepted["parent_edges"]:
        tgt = cid_by_oid.get(e["child_id"])
        parent = name_by_oid.get(e["parent_id"])
        if tgt is not None and parent is not None:
            tgt["object"]["parent"] = parent
    for e in accepted["related_edges"]:
        src = cid_by_oid.get(e["source_id"])
        rel = name_by_oid.get(e["target_id"])
        if src is not None and rel is not None:
            rel_list = src["object"].setdefault("related_to", [])
            if rel not in rel_list:
                rel_list.append(rel)

    linked_questions, link_failures = [], []
    qlinks = {e["question_id"]: e["interest_id"]
              for e in accepted["question_links"]}
    for c in canonical:
        if c["type"] != "question":
            continue
        iid = qlinks.get(c["canonical_id"])
        if iid is None or iid not in name_by_oid:
            link_failures.append({
                "source_id": c["canonical_id"],
                "reason": "required-link unresolved: no accepted "
                          "question->interest edge"})
            dispositions_extra.append({
                "source_id": c["canonical_id"], "decision": "quarantined",
                "target_id": None,
                "reason": "required question->interest link unresolved"})
            continue
        q = json.loads(json.dumps(c["object"]))
        q["interest"] = name_by_oid[iid]
        linked_questions.append(q)
        dispositions_extra.append({
            "source_id": c["canonical_id"], "decision": "linked",
            "target_id": iid, "reason": ""})

    linked_regrets, regret_failures = [], []
    rlinks = {e["regret_id"]: e["interest_id"]
              for e in accepted["regret_links"]}
    for c in canonical:
        if c["type"] != "regret":
            continue
        r = json.loads(json.dumps(c["object"]))
        iid = rlinks.get(c["canonical_id"])
        rel_name = name_by_oid.get(iid) if iid else None
        r["related_interests"] = [rel_name] if rel_name else []
        linked_regrets.append(r)
        dispositions_extra.append({
            "source_id": c["canonical_id"], "decision": "linked"
            if rel_name else "unlinked_optional",
            "target_id": iid, "reason": ""})

    final_interests = []
    for c in interests:
        it = json.loads(json.dumps(c["object"]))
        # fields deliberately absent from phase-1 get their v1 defaults:
        it.setdefault("parent", None)
        it.setdefault("related_to", [])
        final_interests.append(it)

    assembled = {
        "inferred_interests": final_interests,
        "questions": linked_questions,
        "regret_candidates": linked_regrets,
    }
    receipts = {
        "required_link_failures": link_failures,
        "regret_unlinked": [d for d in dispositions_extra
                            if d["decision"] == "unlinked_optional"],
    }
    return assembled, receipts


def sanity_check_assembled(assembled, eligible_cluster_ids, validator):
    """Final gate: run the caller's strict FROZEN v1 validator unchanged.

    The validator callable is injected so the gate always executes in the
    exact module instance the pipeline already loaded (avoids the
    dual-module exception-class trap).
    """
    validator(assembled, set(eligible_cluster_ids))
    return True
