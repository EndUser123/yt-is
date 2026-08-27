"""Interest Intelligence semantic evaluator (ISEM v1) — frozen core.

Scores an inference result against the Interest ground-truth contract
v1.1 (public policy + aggregate receipt in
docs/handoffs/interest-intelligence/interest-ground-truth-v1.1/). The
private holdout itself is NEVER read by this module's design-time work;
at score time only the --gt path handed in is opened, and its sealed
sha256 must match SEALED_GT_SHA256 below.

Preregistration status: the matching policy, scorability policy,
denominators, precision/recall definitions, negative policy, unknown-topic
handling, provenance rules, stability schemes, verdict logic, and
minimum-sample rules frozen here are defined in
docs/handoffs/interest-intelligence/interest-semantic-evaluator-v1/
METRIC_PLAN_PREREGISTRATION.md. Do not edit behavior without recording a
post-hoc AMENDMENT there.

Design boundaries:
  - Every semantic type scores SEPARATELY. No combined recall number.
  - Only `InterestNegative` targets score Interest-classifier false
    positives; every other negative class reports unmachinable-in-track
    and is never folded into an Interest denominator.
  - Ground-truth validity and corpus scorability are independent.
    Scorability consults ONLY the label's own tri-state field plus a
    mechanical evidence-cluster needle probe; inference output is never
    consulted for scorability.
  - The semantic judge is blinded: one candidate object vs one reference
    object, no target status, no arm identity, no thresholds, no
    aggregates. Prompt text and model/config are frozen constants.
  - A matched hit without valid provenance is reported but does NOT count
    toward gate recall (`recall_provenance_ok`); gross recall is reported
    alongside it.
  - Small denominators return INSUFFICIENT_EVIDENCE rather than PASS/FAIL
    at MIN_N_PER_TYPE=5; exact per-item outcomes are always reported.

Pure logic + one subprocess seam: judge transport is injectable so tests
run fully offline. Integrity helpers hash this file family; score time
verifies FROZEN_MANIFEST to prove the plan did not drift since freeze.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

SEALED_GT_SHA256 = (
    "1c7885081dcb6a61e419273c42b3326428727f6718f47736582717b8535aa48f")

SEMANTIC_CLASSES = (
    "Interest", "Goal", "InformationNeed", "Question",
    "InterestNegative", "GoalNegative", "InformationNeedNegative",
    "SourceNegative", "ImplementationConstraintNegative",
    "ToolOrMechanismPreference", "StyleOrProcessPreference",
    "AmbiguousNegative",
)
# Retyped positive-side entries carried outside the Interest contract
# (v1.1 policy delta 2). They bind mechanically but NEVER open a
# scoring track of any kind.
RETYPED_OUTSIDE_CONTRACT = (
    "WorkPreference", "ImplementationConstraint", "Observation")
SEMANTIC_CLASSES = SEMANTIC_CLASSES + RETYPED_OUTSIDE_CONTRACT
POSITIVE_CONTRACT_CLASSES = ("Interest", "Goal", "InformationNeed",
                             "Question")
INTEREST_SCORING_NEGATIVE_CLASSES = ("InterestNegative",)

SCORABLE = "SCORABLE"
UNSCORABLE_MISSING_EVIDENCE = "UNSCORABLE_MISSING_EVIDENCE"
SCORABILITY_UNKNOWN = "SCORABILITY_UNKNOWN"
GT_SCORABILITY_FIELDS = ("corpus_scorable", "corpus_unscorable", "unknown")

MATCH_EXACT = "exact"
MATCH_ALIAS = "alias"
MATCH_JUDGE = "semantic_judge"
MATCH_NONE = "no_match"
_MATCH_RANK = {MATCH_EXACT: 0, MATCH_ALIAS: 1, MATCH_JUDGE: 2}

SURFACE_FOR_CLASS = {
    "Interest": "interest_core",
    "Goal": "goal_strings",
    "InformationNeed": "info_need_strings",
    "Question": "question_texts",
    "InterestNegative": "interest_core",
}

# ARCHITECT_AMENDMENT_1: each explicit negative class scores ONLY its
# corresponding semantic track. A track's finite-set conformance can be
# made IMPERFECT solely by its own negative class.
NEGATIVE_CLASS_TO_TRACK = {
    "InterestNegative": "Interest",
    "GoalNegative": "Goal",
    "InformationNeedNegative": "InformationNeed",
}
NEG_TRACKS_ORDER = tuple(NEGATIVE_CLASS_TO_TRACK)
SURFACE_FOR_CLASS.update({
    "GoalNegative": "goal_strings",
    "InformationNeedNegative": "info_need_strings",
})

MIN_N_PER_TYPE = 5

JUDGE_MODEL = "gpt-5.6-luna"
JUDGE_REASONING_EFFORT = "low"
JUDGE_TIMEOUT_S = 300
JUDGE_MAX_ATTEMPTS = 2

FROZEN_JUDGE_PROMPT_POSITIVE = """You are a blinded semantic-equivalence judge for a personal knowledge system. You see exactly ONE candidate semantic object and exactly ONE reference semantic object. Decide whether the candidate is substantially ABOUT the same underlying subject matter as the reference — the same pursuit, topic, goal, question, or information need, not merely adjacent, co-occurring, or sharing vocabulary.

Rules:
- Judge subject-matter identity, not usefulness, quality, source trust, tooling, or process preferences.
- If the candidate regulates HOW or WITH WHAT work happens (a constraint, tool, mechanism, or source preference), that is not subject-matter identity unless the reference itself is such a statement.
- Ignore temporal strength (durable vs episodic) and any confidence numbers; they never decide the match.

Return ONLY compact JSON: {"match": true|false, "confidence": <0.0-1.0>}
No prose, no markdown fences.

CANDIDATE OBJECT:
kind: <SURFACE_KIND>
text: <SURFACE_TEXT>
context: <SURFACE_CONTEXT>

REFERENCE OBJECT:
kind: <TARGET_CLASS>
name: <TARGET_NAME>
aliases: <TARGET_ALIASES>"""

FROZEN_JUDGE_PROMPT_NEGATIVE_INTEREST = """You are a blinded semantic-equivalence judge for a personal knowledge system. You see exactly ONE candidate semantic object and exactly ONE reference semantic object. The reference expresses something the person explicitly does NOT treat as a pursued interest (a disinterest with explicit scope).

Decide whether the CANDIDATE asserts the SAME subject matter AS a pursued personal interest/topic of engagement — i.e., whether including the candidate would be the false positive the reference exists to catch.

Do NOT call it a match when the candidate merely:
- mentions the subject neutrally or as background,
- states a rejection/constraint mirror ("I don't care about X framing, I care about Y") — that dually supports a constraint record and must score as constraint, not interest,
- judges a tool, mechanism, source, implementation approach, or style related to X,
or when it pursues a DIFFERENT subject matter than the reference.

Return ONLY compact JSON: {"match": true|false, "confidence": <0.0-1.0>}
No prose, no markdown fences.

CANDIDATE OBJECT:
kind: <SURFACE_KIND>
text: <SURFACE_TEXT>
context: <SURFACE_CONTEXT>

REFERENCE OBJECT (disinterest statement):
kind: <TARGET_CLASS>
name: <TARGET_NAME>
aliases: <TARGET_ALIASES>"""

STOP_TOKENS = {"the", "and", "for", "with", "from", "about", "using",
               "into", "that", "this"}


def normalize_text(value: str) -> str:
    """Frozen normalization: NFKC -> casefold -> collapse whitespace."""
    s = value.encode("utf-8", errors="ignore").decode("utf-8")
    s = unicodedata_nfkc(s)
    return " ".join(s.casefold().split())


def unicodedata_nfkc(s: str) -> str:
    try:
        import unicodedata
        return unicodedata.normalize("NFKC", s)
    except Exception:  # pragma: no cover - stdlib cannot fail here
        return s


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Ground truth loading (synonym-tolerant, fail-closed)
# ---------------------------------------------------------------------------

_CLASS_ALIASES = {
    "interest": "Interest", "goal": "Goal",
    "informationneed": "InformationNeed", "question": "Question",
    "interestnegative": "InterestNegative", "goalnegative": "GoalNegative",
    "informationneednegative": "InformationNeedNegative",
    "sourcenegative": "SourceNegative",
    "implementationconstraintnegative":
        "ImplementationConstraintNegative",
    "toolormechanismpreference": "ToolOrMechanismPreference",
    "styleorprocesspreference": "StyleOrProcessPreference",
    "ambiguousnegative": "AmbiguousNegative",
    "workpreference": "WorkPreference",
    "implementationconstraint": "ImplementationConstraint",
    "observation": "Observation",
}
_ROOT_KEYS = ("labels", "targets", "items", "entries", "ground_truth")
_NAME_KEYS = ("canonical_name", "name", "label")
_CLASS_KEYS = ("semantic_class", "class", "type", "kind")
_STATEMENT_KEYS = ("statement_text", "statement", "verbatim_quote",
                   "text")
_SCORABILITY_KEYS = ("scorability_field", "scorability",
                     "corpus_scorability")
_ID_KEYS = ("label_id", "target_id", "id")


def _first_key(item: dict, keys) -> object:
    for k in keys:
        if k in item and item[k] is not None:
            return item[k]
    return None


def _bind_class(raw) -> str | None:
    if raw is None:
        return None
    key = str(raw).strip().casefold().replace("-", "_").replace(" ", "_")
    key = key.replace("_negative", "negative")
    return _CLASS_ALIASES.get(key.replace(" ", ""))


class SchemaBindError(Exception):
    """GT artifact cannot be mapped without new field-level decisions."""


def load_ground_truth(path) -> dict:
    """Bind the holdout artifact onto the canonical GT schema.

    Synonym-tolerant over KEY NAMES only; value-level interpretation is
    fixed. Ambiguity or missing classes raise SchemaBindError — the run
    fails closed instead of improvising a post-unseal mapping decision.
    """
    raw_path = Path(path)
    digest = sha256_file(raw_path)
    doc = json.loads(raw_path.read_text(encoding="utf-8"))

    rows = None
    for k in _ROOT_KEYS:
        if isinstance(doc.get(k), list):
            rows = doc[k]
            break
    if rows is None and isinstance(doc.get("negatives"), dict):
        merged: list = []
        for group in doc["negatives"].values():
            merged.extend(group if isinstance(group, list) else [])
        rows = merged or None
    if rows is None:
        # tolerate a bare list document
        if isinstance(doc, list):
            rows = doc
    if rows is None:
        raise SchemaBindError(
            f"no known root container in {raw_path.name}; "
            f"keys={sorted(doc)[:12]}")

    labels = []
    seen_ids = set()
    for idx, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        cls = _bind_class(_first_key(item, _CLASS_KEYS))
        name = _first_key(item, _NAME_KEYS)
        if cls is None or not isinstance(name, str) or not name.strip():
            raise SchemaBindError(
                f"row {idx}: unbindable class/name "
                f"(keys={sorted(item)[:12]})")
        if cls not in SEMANTIC_CLASSES:
            raise SchemaBindError(f"row {idx}: unknown class {cls!r}")
        lid = _first_key(item, _ID_KEYS) or f"{cls.lower()}_{idx}"
        lid = str(lid)
        if lid in seen_ids:
            raise SchemaBindError(f"duplicate label id {lid!r}")
        seen_ids.add(lid)

        aliases_raw = item.get("aliases") or []
        if not isinstance(aliases_raw, list):
            raise SchemaBindError(f"{lid}: aliases must be a list")

        sc_raw = _first_key(item, _SCORABILITY_KEYS)
        sc_field = None
        if isinstance(sc_raw, bool):
            sc_field = "corpus_scorable" if sc_raw else None
        elif isinstance(sc_raw, str):
            keyv = sc_raw.strip().casefold()
            if keyv in GT_SCORABILITY_FIELDS:
                sc_field = keyv
            elif keyv in ("true", "scorable"):
                sc_field = "corpus_scorable"
            elif keyv in ("false", "unscorable"):
                sc_field = "corpus_unscorable"
            else:
                raise SchemaBindError(
                    f"{lid}: unusable scorability field {sc_raw!r}")

        probes = item.get("probe_receipts") or []
        if not isinstance(probes, list):
            probes = [str(probes)]
        statement = _first_key(item, _STATEMENT_KEYS)

        labels.append({
            "label_id": lid,
            "semantic_class": cls,
            "canonical_name": name,
            "aliases": [str(a) for a in aliases_raw],
            "statement_text": statement if isinstance(statement, str)
                              else None,
            "authority": item.get("authority"),
            "scorability_field": sc_field,
            "probe_receipts": [str(p) for p in probes],
        })
    return {
        "source_path": str(raw_path),
        "sealed_sha256": digest,
        "labels": labels,
    }


def verify_sealed(gt_doc: dict) -> None:
    digest = gt_doc["sealed_sha256"]
    if digest != SEALED_GT_SHA256:
        raise SchemaBindError(
            "ground truth sha256 mismatch: expected sealed "
            f"{SEALED_GT_SHA256}, found {digest} — refusing to score")


# ---------------------------------------------------------------------------
# Inference result view
# ---------------------------------------------------------------------------


class ResultView:
    """Typed surfaces extracted from a validated inference payload."""

    def __init__(self, payload: dict, eligible_cluster_ids=None,
                 warnings=None):
        self.warnings = warnings or []
        interests_all = payload.get("inferred_interests") or []
        self.adjacent_names = set()
        core = []
        adjacent = []
        for it in interests_all:
            ovi = it.get("observed_vs_inferred")
            if ovi == "inferred_adjacent":
                adjacent.append(it)
                self.adjacent_names.add(it.get("name"))
            else:
                core.append(it)

        def refs_of(interest):
            refs = interest.get("cluster_ids") or []
            ok, bad = [], 0
            eligible = eligible_cluster_ids
            for r in refs:
                if eligible is not None and r not in eligible:
                    bad += 1
                else:
                    ok.append(r)
            if not refs:
                state = "missing_refs"
            elif bad:
                state = "invalid_refs"
            else:
                state = "valid"
            return ok, state

        # -- interest surfaces -------------------------------------------------
        self.interest_core = []          # dicts: sid,text,context,refs,prov
        adjacent_view = []
        for it in core:
            refs, prov = refs_of(it)
            summary = (it.get("evidence_summary") or "").strip()
            self.interest_core.append({
                "sid": f"I{len(self.interest_core)}",
                "text": it.get("name") or "",
                "context": summary[:400],
                "refs": refs,
                "provenance": prov,
            })
        for it in adjacent:
            refs, prov = refs_of(it)
            adjacent_view.append({
                "sid": f"A{len(adjacent_view)}",
                "text": it.get("name") or "",
                "context": (it.get("evidence_summary") or "")[:400],
                "refs": refs, "provenance": prov,
            })

        interest_by_name = {}
        for it in core:
            norm = normalize_text(it.get("name") or "")
            if norm:
                interest_by_name.setdefault(norm, []).append(it)

        # -- goal / information_need surfaces ----------------------------------
        def collect(field: str) -> list[dict]:
            out, seen = [], {}
            for i, surf in enumerate(self.interest_core):
                owner = core[i]
                val = owner.get(field)
                if not isinstance(val, str) or not val.strip():
                    continue
                ctx = f"stated under interest '{owner.get('name')}'"
                key = normalize_text(val)
                if key in seen:
                    seen[key]["owner_sids"].append(surf["sid"])
                    continue
                refs, prov = refs_of(owner)
                entry = {"sid": f"{field[0].upper()}{len(out)}",
                         "text": val.strip(), "context": ctx,
                         "owner_sids": [surf["sid"]],
                         "refs": refs, "provenance": prov}
                seen[key] = entry
                out.append(entry)
            return out

        self.goal_strings = collect("goal")
        self.info_need_strings = collect("information_need")

        # -- question surfaces --------------------------------------------------
        questions = payload.get("questions") or []
        q_out, q_seen = [], {}
        for q in questions:
            text = (q.get("text") or "").strip()
            if not text:
                continue
            key = normalize_text(text)
            parent = interest_by_name.get(
                normalize_text(q.get("interest") or ""))
            owner = parent[0] if parent else None
            refs, prov = (("P", []) , "missing_parent_interest") \
                if owner is None else refs_of(owner)
            if key in q_seen:
                q_seen[key]["dup_count"] += 1
                continue
            entry = {"sid": f"Q{len(q_out)}", "text": text,
                     "context": "", "refs": refs, "provenance": prov,
                     "dup_count": 0}
            q_seen[key] = entry
            q_out.append(entry)
        self.question_texts = q_out

        self.regret_candidates = payload.get("regret_candidates") or []
        self.n_interest_adjacent = len(adjacent_view)
        self.n_regret = len(self.regret_candidates)
        self.dispositions = payload.get("fragment_dispositions")

    @classmethod
    def from_payload(cls, payload: dict,
                     eligible_cluster_ids=None, warnings=None):
        inner = payload
        disp = None
        if isinstance(payload.get("final"), dict):
            inner = payload["final"]
            disp = payload.get("fragment_dispositions")
        view = cls(inner, eligible_cluster_ids=eligible_cluster_ids,
                   warnings=warnings)
        if disp is not None:
            view.dispositions = disp
        return view

    def surfaces_for(self, track: str) -> list[dict]:
        return getattr(self, SURFACE_FOR_CLASS[track])


# ---------------------------------------------------------------------------
# Matching pipeline
# ---------------------------------------------------------------------------


def _sig_tokens(text: str) -> set[str]:
    toks = set()
    for w in normalize_text(text).replace("/", " ").split():
        w2 = re.sub(r"[^0-9a-z]", "", w)
        if len(w2) >= 5 and w2 not in STOP_TOKENS and not w2.isdigit():
            toks.add(w2)
    return toks


def alias_hit(surface_text: str, context: str, target: dict) -> bool:
    needles = [normalize_text(target["canonical_name"])] + [
        normalize_text(a) for a in target.get("aliases", [])
        if len(a.strip()) >= 4]
    hay = normalize_text(f"{surface_text} | {context}")
    for n in needles:
        if len(n) >= 4 and n in hay:
            return True
    toks = _sig_tokens(target["canonical_name"])
    if len(toks) >= 2:
        hay_toks = _sig_tokens(hay)
        if toks <= hay_toks:
            return True
    return False


def render_judge_prompt(template: str, surface_kind: str,
                        surface_text: str, surface_context: str,
                        target_class: str, target_name: str,
                        target_aliases: str) -> str:
    """Token substitution; prompts embed literal JSON braces."""
    return (template
            .replace("<SURFACE_KIND>", surface_kind)
            .replace("<SURFACE_TEXT>", surface_text)
            .replace("<SURFACE_CONTEXT>", surface_context)
            .replace("<TARGET_CLASS>", target_class)
            .replace("<TARGET_NAME>", target_name)
            .replace("<TARGET_ALIASES>", target_aliases))


def match_one(target: dict, surface: dict, is_negative_target: bool,
              judge) -> tuple[str, object]:
    """exact -> alias -> semantic_judge -> no_match for one pair."""
    t_norm = normalize_text(target["canonical_name"])
    s_norm = normalize_text(surface["text"])
    if t_norm == s_norm:
        return MATCH_EXACT, None
    if alias_hit(surface["text"], surface["context"], target):
        return MATCH_ALIAS, None
    prompt = (FROZEN_JUDGE_PROMPT_NEGATIVE_INTEREST
              if is_negative_target else FROZEN_JUDGE_PROMPT_POSITIVE)
    rendered = render_judge_prompt(
        prompt,
        surface_kind=surface.get("surface_kind_override")
        or SURFACE_FOR_CLASS[target["semantic_class"]]
        .replace("_core", "").replace("_strings", "")
        .replace("_texts", ""),
        surface_text=surface["text"],
        surface_context=surface["context"] or "(none)",
        target_class=target["semantic_class"],
        target_name=target["canonical_name"],
        target_aliases=", ".join(target.get("aliases", []) or []))
    verdict = judge(rendered, surface, target)
    if verdict is True:
        return MATCH_JUDGE, None
    if verdict is False:
        return MATCH_NONE, None
    return MATCH_NONE, "judge_error"


def run_track(track_class: str, targets: list[dict], surfaces: list[dict],
              judge) -> dict:
    """Greedy best-path assignment; targets processed in listed order."""
    negatives = track_class in INTEREST_SCORING_NEGATIVE_CLASSES
    outcomes = []
    consumed = set()
    for target in targets:
        chosen = None
        chosen_path = MATCH_NONE
        notes = []
        ranked = sorted(
            [(surf, surface_rank) for surface_rank, surf in
             enumerate(surfaces)],
            key=lambda sr: (_match_score(sr[0], target), sr[1]))
        best = MATCH_NONE
        best_surface = None
        best_err = None
        for surf, _rank in ranked:
            if id(surf) in consumed:
                continue
            path, err = match_one(target, surf, negatives, judge)
            path_rank = _MATCH_RANK.get(path, 3)
            cur_rank = _MATCH_RANK.get(best, 3)
            if err:
                notes.append({"surface_id": surf["sid"],
                              "error": err})
            if path != MATCH_NONE and path_rank < cur_rank:
                best, best_surface, best_err = path, surf, err
        if best_surface is not None:
            consumed.add(id(best_surface))
            chosen, chosen_path = best_surface, best
        outcomes.append({
            "target_id": target["label_id"],
            "matched_surface_id":
                chosen["sid"] if chosen else None,
            "matching_path": chosen_path,
            "judge_error_notes": notes,
        })
    matched_sids = {o["matched_surface_id"] for o in outcomes}
    extra = [s["sid"] for s in surfaces
             if s["sid"] not in matched_sids]
    return {"track_class": track_class,
            "outcomes": outcomes,
            "extra_surfaces_unmatched": extra,
            "n_surfaces": len(surfaces)}


def _match_score(surface: dict, target: dict) -> int:
    t_norm = normalize_text(target["canonical_name"])
    s_norm = normalize_text(surface["text"])
    if t_norm == s_norm:
        return 0
    if alias_hit(surface["text"], surface["context"], target):
        return 1
    return 2  # judge tier resolved later per-pair


class _JudgeRecorder:
    def __init__(self, fn, log):
        self.fn, self.log = fn, log

    def __call__(self, prompt_text, surface, target):
        key = sha256_bytes(json.dumps(
            [sha256_bytes(prompt_text.encode("utf-8")),
             surface["sid"], target["label_id"]],
            sort_keys=True).encode())
        out = self.fn(prompt_text, surface, target)
        self.log.append({"pair_hash": key,
                         "candidate_sid": surface["sid"],
                         "target_id": target["label_id"],
                         "result": ("match" if out is True else
                                    "no_match" if out is False else
                                    "error")})
        return out


def judge_transport_factory(cache_path=None):
    """Frozen codex-exec judge transport (blinded, cached)."""
    cache = {}
    cache_p = Path(cache_path) if cache_path else None
    if cache_p and cache_p.exists():
        try:
            cache = json.loads(cache_p.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    def transport(prompt_text, surface, target):
        key = sha256_bytes(prompt_text.encode("utf-8"))
        if key in cache:
            return cache[key]
        import shutil
        import tempfile
        codex = shutil.which("codex")
        if not codex:
            return None
        attempts_ok = False
        for _attempt in range(JUDGE_MAX_ATTEMPTS):
            pf = Path(tempfile.gettempdir()) / (
                f"isem-judge-{key[:16]}.txt")
            pf.write_text(prompt_text, encoding="utf-8")
            try:
                r = subprocess.run(
                    [codex, "exec", "--json", "--ephemeral",
                     "-s", "read-only", "-m", JUDGE_MODEL,
                     "-c",
                     f"model_reasoning_effort={JUDGE_REASONING_EFFORT}",
                     "-C", "P:/",
                     f"Read {pf} and return ONLY the JSON. No prose, "
                     "no markdown fences."],
                    capture_output=True, text=True,
                    timeout=JUDGE_TIMEOUT_S, cwd="P:/",
                    creationflags=getattr(
                        subprocess, "CREATE_NO_WINDOW", 0))
            except (subprocess.TimeoutExpired, OSError):
                continue
            if r.returncode != 0:
                continue
            parsed = _extract_judge_json(r.stdout)
            if parsed is None:
                continue
            val = bool(parsed.get("match"))
            cache[key] = val
            attempts_ok = True
            break
        if cache_p:
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            cache_p.write_text(json.dumps(cache, indent=1, sort_keys=True),
                               encoding="utf-8")
        if not attempts_ok:
            return None
        return cache[key]

    return transport


def _extract_judge_json(stdout_text: str):
    objs = re.findall(r"\{[^{}]*\}", stdout_text)
    for o in reversed(objs):
        try:
            j = json.loads(o)
            if isinstance(j.get("match"), bool):
                return j
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# Scorability
# ---------------------------------------------------------------------------


def classify_scorability(label: dict, support_probe_hit: bool) -> str:
    """Combine the label's own tri-state field with the mechanical probe.

    Inference output is NEVER consulted here.
    """
    field = label.get("scorability_field")
    if field == "corpus_scorable":
        return SCORABLE
    if field == "corpus_unscorable":
        return UNSCORABLE_MISSING_EVIDENCE
    if field == "unknown" or field is None:
        return SCORABLE if support_probe_hit else SCORABILITY_UNKNOWN
    return SCORABILITY_UNKNOWN


def needle_support(target: dict, cluster_texts: dict[int, str]) -> list[int]:
    """Deterministic evidence-cluster probe (legacy-compatible)."""
    needles = [normalize_text(target["canonical_name"])] + [
        normalize_text(a) for a in target.get("aliases", [])
        if len(a.strip()) >= 4]
    hits = []
    for cid, text in sorted(cluster_texts.items()):
        hay = normalize_text(text or "")
        if any(len(n) >= 4 and n in hay for n in needles):
            hits.append(cid)
    return hits


# ---------------------------------------------------------------------------
# Metrics and verdict
# ---------------------------------------------------------------------------

PROVENANCE_OK = {"valid"}
PROVENANCE_BAD = {"invalid_refs", "missing_refs"}


def provenance_state_of(view, outcome, surface_index: dict) -> str:
    if outcome["matched_surface_id"] is None:
        return "not_applicable"
    return surface_index[outcome["matched_surface_id"]]["provenance"]


def type_metrics(track_result: dict, targets: list[dict], view,
                 scorability_by_id: dict, surface_lookup: dict,
                 stability_known: bool) -> dict:
    track = track_result["track_class"]
    outcomes = track_result["outcomes"]
    rows = []
    gross_matched = prov_ok_matched = 0
    unsupported_hits = 0
    scorable_n = excluded_unknown = excluded_unscorable = 0
    for outcome in outcomes:
        tgt = next(t for t in targets
                   if t["label_id"] == outcome["target_id"])
        state = scorability_by_id[tgt["label_id"]]
        pstate = provenance_state_of(
            view, outcome, surface_lookup) \
            if outcome["matched_surface_id"] else "not_applicable"
        counted_gross = False
        counted_prov_ok = False
        if tgt["semantic_class"] in POSITIVE_CONTRACT_CLASSES:
            if state == SCORABLE:
                scorable_n += 1
                if outcome["matching_path"] != MATCH_NONE:
                    counted_gross = True
                    gross_matched += 1
                    if pstate in PROVENANCE_OK:
                        counted_prov_ok = True
                        prov_ok_matched += 1
                    else:
                        unsupported_hits += 1
            elif state == UNSCORABLE_MISSING_EVIDENCE:
                excluded_unscorable += 1
            else:
                excluded_unknown += 1
        matched_flag = outcome["matching_path"] != MATCH_NONE
        rows.append({
            "label_id": tgt["label_id"],
            "semantic_class": tgt["semantic_class"],
            "matched": matched_flag,
            "semantic_match": matched_flag,
            "matching_path": outcome["matching_path"],
            "matched_surface_id": outcome["matched_surface_id"],
            "provenance": pstate,
            "provenance_valid_match": bool(matched_flag and pstate
                                           in PROVENANCE_OK),
            "counts_toward_gross_recall": counted_gross,
            "counts_toward_gate_recall": counted_prov_ok,
            "scorability": state,
        })
    denom_gross = scorable_n
    if track in INTEREST_SCORING_NEGATIVE_CLASSES:
        fp_hits = sum(1 for r in rows
                      if r["semantic_class"]
                      in INTEREST_SCORING_NEGATIVE_CLASSES
                      and r["matched"])
    else:
        fp_hits = None
    m = {
        "track": track,
        "n_formal_positives": sum(
            1 for r in rows
            if r["semantic_class"] in POSITIVE_CONTRACT_CLASSES),
        "n_scorable_positives": scorable_n,
        "excluded_unscorable": excluded_unscorable,
        "excluded_scorability_unknown": excluded_unknown,
        "n_candidate_surfaces": track_result["n_surfaces"],
        "recall_gross": (gross_matched / denom_gross)
                         if denom_gross else None,
        "recall_provenance_ok": (prov_ok_matched / denom_gross)
                                 if denom_gross else None,
        "unsupported_matched_hits": unsupported_hits,
        "interest_negative_fp_hits": fp_hits,
        "per_item": rows,
        "per_item_negatives": [r for r in rows
                               if r["semantic_class"]
                               in INTEREST_SCORING_NEGATIVE_CLASSES],
    }
    m["verdict"] = verdict_for(m, stability_known)
    return m


def verdict_for(m: dict, stability_known: bool) -> str:
    if m["n_formal_positives"] == 0:
        return "NOT_APPLICABLE"
    if m["n_scorable_positives"] < MIN_N_PER_TYPE:
        return "INSUFFICIENT_EVIDENCE"
    if m["recall_provenance_ok"] is None:
        return "INSUFFICIENT_EVIDENCE"
    if m["track"] == "Interest":
        if (m["interest_negative_fp_hits"] or 0) > 0:
            return "FAIL"
        if m["recall_provenance_ok"] < 1.0:
            return "FAIL"
        if not stability_known:
            return "INCOMPLETE_PERTURBATION_PENDING"
        return "PASS" if m["recall_provenance_ok"] >= 1.0 else "FAIL"
    return "PASS" if m["recall_provenance_ok"] >= 1.0 else "FAIL"


def evaluate(gt_doc: dict, result_payload: dict, judge,
             eligible_cluster_ids=None, support_hits_by_label=None,
             stability_results=None) -> dict:
    """Full evaluation: five separated tracks, metrics, overall verdict."""
    verify_sealed(gt_doc)
    labels = gt_doc["labels"]
    support_hits_by_label = support_hits_by_label or {}

    view = ResultView.from_payload(result_payload,
                                   eligible_cluster_ids=eligible_cluster_ids)
    surface_lookup = {}
    for key in set(SURFACE_FOR_CLASS.values()):
        for surf in getattr(view, key):
            surface_lookup[surf["sid"]] = surf

    scorability_by_id = {}
    for lab in labels:
        hits = bool(support_hits_by_label.get(lab["label_id"]))
        scorability_by_id[lab["label_id"]] = classify_scorability(
            lab, hits)

    stability_known = bool(stability_results)
    tracks = {}
    for cls in POSITIVE_CONTRACT_CLASSES + NEG_TRACKS_ORDER:
        targets = [lab for lab in labels
                   if lab["semantic_class"] == cls]
        if not targets:
            continue
        surf = view.surfaces_for(cls)
        for s in surf:
            s.setdefault("surface_kind_override", None)
        recorder_log = []
        tr = run_track(cls, targets, surf,
                       _JudgeRecorder(judge, recorder_log))
        tr["judge_calls"] = recorder_log
        tm = type_metrics(tr, targets, view, scorability_by_id,
                          surface_lookup, stability_known)
        tracks[cls] = tm

    # InterestNegative FP hits gate the Interest-family verdict even
    # though the two live in separated tracks (never one denominator).
    if "InterestNegative" in tracks and "Interest" in tracks:
        fp = tracks["InterestNegative"]["interest_negative_fp_hits"]
        tracks["Interest"]["interest_negative_fp_hits"] = fp
        tracks["Interest"]["verdict"] = verdict_for(
            tracks["Interest"], stability_known)

    negative_other = [lab for lab in labels
                      if lab["semantic_class"]
                      not in POSITIVE_CONTRACT_CLASSES
                      and lab["semantic_class"]
                      not in INTEREST_SCORING_NEGATIVE_CLASSES]

    fsc = finite_set_conformance(labels, tracks)

    report = {
        "evaluator": "isem_v1",
        "amendment": "ARCHITECT_AMENDMENT_1",
        "generated": time.strftime("%Y-%m-%dT%H%M%S"),
        "sealed_gt_sha256_verified": SEALED_GT_SHA256,
        "min_n_per_type": MIN_N_PER_TYPE,
        "tracks": tracks,
        "finite_set_conformance": fsc["per_type"],
        "overall_finite_set_conformance": fsc["aggregate"],
        "non_interest_negatives_not_scoring_interest": [
            {"label_id": l["label_id"],
             "semantic_class": l["semantic_class"]}
            for l in negative_other],
        "retyped_outside_contract_counts": {
            cls: sum(1 for lab in labels
                     if lab["semantic_class"] == cls)
            for cls in RETYPED_OUTSIDE_CONTRACT},
        "diagnostics": {
            "n_interest_core_surfaces": len(view.interest_core),
            "n_interest_adjacent_surfaces":
                view.n_interest_adjacent,
            "adjacent_excluded_from_core_denominators": True,
            "n_goal_strings": len(view.goal_strings),
            "n_info_need_strings": len(view.info_need_strings),
            "n_question_texts": len(view.question_texts),
            "n_regret_candidates": view.n_regret,
            "question_duplicate_collapses":
                sum(q.get("dup_count", 0)
                    for q in view.question_texts),
        },
    }
    report["overall_verdict"] = overall_verdict(report, stability_results)
    return report


def finite_set_conformance(labels: list[dict], tracks: dict) -> dict:
    """ARCHITECT_AMENDMENT_1 — exact finite-set result per type.

    Orthogonal to the generalization verdicts above. No percentages,
    no statistical inference, no partial cutoffs:

      PERFECT        every corpus-scorable positive of the type is
                     recovered as a provenance_valid_match AND no
                     explicit matching negative of that type is
                     semantically inferred
      IMPERFECT      >=1 scorable positive missed (semantic or
                     provenance) OR >=1 matching negative inferred
      NOT_EVALUABLE  zero corpus-scorable labels of that type exist
                     (no scorable positives and no negatives assigned
                     to the type)

    FINITE_SET_CONFORMANCE uses provenance_valid_match; semantic_match
    is reported alongside it. Only a type's own negative class can make
    it IMPERFECT.
    """
    per_type = {}
    for cls in POSITIVE_CONTRACT_CLASSES:
        tm = tracks.get(cls)
        pos_rows = [r for r in (tm["per_item"] if tm else [])
                    if r["semantic_class"] == cls]
        scorable_rows = [r for r in pos_rows
                         if r["scorability"] == SCORABLE]
        prov_ok_recovered = [r for r in scorable_rows
                             if r["provenance_valid_match"]]
        semantic_only_missed = [
            r for r in scorable_rows if not r["provenance_valid_match"]]

        neg_rows_all = []
        for ncls, target_track in NEGATIVE_CLASS_TO_TRACK.items():
            if target_track != cls:
                continue
            ntm = tracks.get(ncls)
            neg_rows_all.extend(ntm["per_item"] if ntm else [])
        neg_hits = [r["label_id"] for r in neg_rows_all
                    if r["matched"]]

        items = []
        for r in pos_rows:
            items.append({
                "label_id": r["label_id"],
                "role": "positive",
                "scorability": r["scorability"],
                "semantic_match": r["semantic_match"],
                "matching_path": r["matching_path"],
                "provenance": r["provenance"],
                "provenance_valid_match": r["provenance_valid_match"],
                "missed": bool(r["scorability"] == SCORABLE
                               and not r["provenance_valid_match"]),
            })
        for r in neg_rows_all:
            items.append({
                "label_id": r["label_id"],
                "role": "negative",
                "semantic_class": r["semantic_class"],
                "semantically_inferred": r["matched"],
                "matching_path": r["matching_path"],
                "hit": r["matched"],
            })

        n_neg_labels = len(neg_rows_all)
        if not scorable_rows and n_neg_labels == 0:
            status = "NOT_EVALUABLE"
        elif not scorable_rows and n_neg_labels > 0:
            # zero scorable positives: only the negative side decides;
            # recorded mechanically rather than silently elided
            status = "IMPERFECT" if neg_hits else "PERFECT"
        else:
            status = ("PERFECT"
                      if len(prov_ok_recovered) == len(scorable_rows)
                      and not neg_hits else "IMPERFECT")
        per_type[cls] = {
            "status": status,
            "n_scorable_positives": len(scorable_rows),
            "provenance_valid_recovered": len(prov_ok_recovered),
            "missed_scorable_positives": len(semantic_only_missed),
            "explicit_negative_hits": neg_hits,
            "n_negative_labels_of_type": n_neg_labels,
            "items": items,
        }
    aggregate = {
        "perfect": [c for c, v in per_type.items()
                    if v["status"] == "PERFECT"],
        "imperfect": [c for c, v in per_type.items()
                      if v["status"] == "IMPERFECT"],
        "not_evaluable": [c for c, v in per_type.items()
                          if v["status"] == "NOT_EVALUABLE"],
    }
    return {"per_type": per_type, "aggregate": aggregate}


def overall_verdict(report: dict, stability_results) -> str:
    verdicts = [t["verdict"] for t in report["tracks"].values()]
    if not verdicts:
        return "NO_EVALUABLE_TYPES"
    if all(v == "NOT_APPLICABLE" for v in verdicts):
        return "NO_EVALUABLE_TYPES"
    if all(v == "INSUFFICIENT_EVIDENCE" for v in verdicts):
        return "DIAGNOSTIC_ONLY_INSUFFICIENT_EVIDENCE"
    if any(v == "FAIL" for v in verdicts):
        return "MIXED_WITH_FAIL"
    if any(v == "PASS" for v in verdicts):
        if stability_results:
            return "SUFFICIENT_PASS"
        return "PARTIAL_PERTURBATION_PENDING"
    return "MIXED"


# ---------------------------------------------------------------------------
# Stability perturbations (frozen definitions, applied post-freeze)
# ---------------------------------------------------------------------------

STABILITY_SEED_DROP5 = 1337
STABILITY_SEED_ORDER = 20260826


def stability_variants(cluster_inventory: dict) -> dict:
    """Build the preregistered deterministic variants.

    S1_RANDOM_DROP_5PCT  seed 1337, min 8 removed (legacy-compatible)
    S2_TOP_BREADTH_DROP_10   ten highest channel breadth clusters
    S3_REPS_TRIM         representative docs truncated to first two
    S4_ORDER_SHUFFLE     packet-order shuffle, seed 20260826
    """
    clusters = cluster_inventory["clusters"]
    ids = [c["cluster_id"] for c in clusters]
    rng = random.Random(STABILITY_SEED_DROP5)
    s1_removed = rng.sample(ids, max(8, round(0.05 * len(ids))))
    s2_removed = [c["cluster_id"] for c in
                  sorted(clusters, key=lambda c: -c["channels"])[:10]]
    s3_clusters = []
    for c in clusters:
        c2 = dict(c)
        reps = c.get("representative") or []
        c2["representative"] = reps[:2]
        s3_clusters.append(c2)
    s4_clusters = list(clusters)
    random.Random(STABILITY_SEED_ORDER).shuffle(s4_clusters)

    def variant(name, keep_clusters, removed):
        inv = dict(cluster_inventory)
        inv["clusters"] = keep_clusters
        manifest = {"scheme": name,
                    "removed_cluster_ids_sha256":
                        sha256_bytes(json.dumps(sorted(removed),
                                                sort_keys=True).encode()),
                    "removed_count": len(removed),
                    "seed_or_rule": name}
        return inv, manifest

    out = {}
    out["S1_RANDOM_DROP_5PCT"], man1 = variant(
        "S1_RANDOM_DROP_5PCT",
        [c for c in clusters if c["cluster_id"] not in set(s1_removed)],
        s1_removed)
    out["S2_TOP_BREADTH_DROP_10"], man2 = variant(
        "S2_TOP_BREADTH_DROP_10",
        [c for c in clusters if c["cluster_id"] not in set(s2_removed)],
        s2_removed)
    out["S3_REPS_TRIM"], man3 = variant(
        "S3_REPS_TRIM", s3_clusters, [])
    out["S4_ORDER_SHUFFLE"], man4 = variant(
        "S4_ORDER_SHUFFLE", s4_clusters, [])
    return {"variants": out,
            "manifests": [man1, man2, man3, man4]}


def compare_matched_sets(base_report: dict, variant_reports: dict)\
        -> dict:
    """A scheme is stable iff the matched target-id set is unchanged."""
    base_sets = {}
    for cls, tm in base_report["tracks"].items():
        base_sets[cls] = frozenset(
            r["label_id"] for r in tm["per_item"] if r["matched"])
    out = {}
    for name, rep in variant_reports.items():
        diffs = []
        for cls, tm in rep["tracks"].items():
            var_set = frozenset(r["label_id"] for r in tm["per_item"]
                                if r["matched"])
            gone = sorted(base_sets.get(cls, set()) - var_set)
            new = sorted(var_set - base_sets.get(cls, set()))
            if gone or new:
                diffs.append({"track": cls, "lost": gone,
                              "gained": new})
        out[name] = {"stable": not diffs, "diffs": diffs}
    return out
