"""Recommendation regret evaluator (RRE v1) — frozen core.

Freezes HOW we will later tell whether a ranking challenger improves
recommendations. It is an evaluator ONLY: it never ranks, scores,
trains, or optimizes a policy, and it contains no utility-weight
arithmetic anywhere by design.

Adopts the ISEM v1 lesson (docs/handoffs/interest-intelligence/
interest-semantic-evaluator-v1/): every evaluation returns BOTH
  A. FINITE_SET_OUTCOME — exact deterministic accounting over the
     packets actually judged; and
  B. GENERALIZATION_STATUS — SUFFICIENT_EVIDENCE only above frozen
     minimum-sample thresholds, else INSUFFICIENT_EVIDENCE.
Small n implies neither "we learned nothing" nor population-level
confidence.

Preregistration status: outcome vocabulary, question separation,
dimension definitions, blinding scheme, provenance validity, feedback
role mapping, metrics, tie handling, small-n rules, and the primary
falsifier are defined in docs/handoffs/interest-intelligence/
recommendation-regret-evaluator-v1/METRIC_PLAN_PREREGISTRATION.md.
Behavior changes after freeze are recorded there as AMENDMENT entries;
unmarked drift invalidates the run (the CLI receipt check makes drift
mechanically visible).

Contamination boundaries honored at design time:
  - no future ranking outputs exist or were inspected (none built);
  - no private holdout packet was created here — curation belongs to a
    later independent curator AFTER this evaluator freezes;
  - Interest Ground Truth v1.1 labels are never used as recommendation
    labels;
  - no tuning on feedback-event outcomes: feedback enters only through
    the frozen role map below, and nothing here consumes outcomes as a
    reward or training signal.

Off-policy honesty: historical impressions have propensity UNKNOWN.
This module can REPORT observed online feedback counts tagged
`unknown_propensity`, and structurally provides no estimator that could
present sparse non-random history as an unbiased offline ranking
estimate.

Pure logic + one subprocess seam: judge transport is injectable so all
tests run fully offline. Integrity helpers hash this file family.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

EVALUATOR_ID = "rre_v1"
BLIND_SALT = "rre_v1_slot_blind_salt_2026_08_27"

QUESTIONS = ("WOULD_REGRET_MISSING", "MORE_USEFUL_NOW")
PRIMARY_QUESTION = "WOULD_REGRET_MISSING"

# Presentation-slot outcome vocabulary for BOTH questions.
OUTCOME_ITEM_1 = "ITEM_1"
OUTCOME_ITEM_2 = "ITEM_2"
OUTCOME_TIE = "TIE"
OUTCOME_NEITHER = "NEITHER"
OUTCOME_VALUES = (OUTCOME_ITEM_1, OUTCOME_ITEM_2, OUTCOME_TIE,
                  OUTCOME_NEITHER)

DIMENSION_RATINGS = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")

# The nine preregistered judgment dimensions (exact names frozen).
# Orientation records which direction is "better"; orientation is
# metadata for reporting only — nothing in this module aggregates
# dimensions into one relevance score (deliberately no such function).
DIMENSIONS = (
    {"name": "regret_if_missed", "orientation": "+",
     "definition":
         "If the operator never saw this item, how much would they "
         "later wish they had? High = plausibly consequential loss."},
    {"name": "relevance_to_active_goal", "orientation": "+",
     "definition":
         "Strength of traceable relation to an operator Goal / Interest "
         "/ InformationNeed recorded in item.related_records."},
    {"name": "novelty_to_user", "orientation": "+",
     "definition":
         "Genuinely new to THIS operator relative to recent_surfaced_"
         "items and known background, independent of value."},
    {"name": "consequence", "orientation": "+",
     "definition":
         "Real-world stakes if acted on or missed: decisions enabled, "
         "costs avoided, opportunities opened/closed."},
    {"name": "actionability", "orientation": "+",
     "definition":
         "A concrete next action exists now for this operator."},
    {"name": "evidence_quality", "orientation": "+",
     "definition":
         "How well the item's claims are supported by its cited "
         "evidence refs and how trustworthy those sources are."},
    {"name": "redundancy", "orientation": "-",
     "definition":
         "Substantially repeats another recently surfaced item "
         "(recent_surfaced_items) in claim content, not merely topic."},
    {"name": "known_already", "orientation": "-",
     "definition":
         "The operator already knows this content; confirming repetition "
         "adds little even when accurate."},
    {"name": "timing_appropriateness", "orientation": "+",
     "definition":
         "NOW is a good time to surface it (window open, not stale, not "
         "premature)."},
)
DIMENSION_NAMES = tuple(d["name"] for d in DIMENSIONS)
PENALTY_DIMENSIONS = ("redundancy", "known_already")

# Stylized quadrant profiles that must stay distinct (never collapse).
QUADRANT_REDUNDANT_RECENT = "REDUNDANT_WITH_RECENT_SURFACE"
QUADRANT_USEFUL_KNOWN = "USEFUL_BUT_ALREADY_KNOWN"
QUADRANT_IMPORTANT_NOVEL = "IMPORTANT_AND_NOVEL"
QUADRANT_NOVEL_LOW_VALUE = "NOVEL_BUT_LOW_VALUE"
QUADRANT_MIXED_UNCLASSIFIED = "MIXED_UNCLASSIFIED"

MIN_DECIDED_PAIRS_FOR_GENERALIZATION = 5
MIN_DISTINCT_PACKETS_FOR_GENERALIZATION = 5

SIMILARITY_CONFOUNDED_DELTA_MAX = None  # see primary_falsifier_verdict:
# confounding is decided per-pair (a win on lower similarity), not by a
# mean-delta threshold.

# Feedback verdict -> evaluation role (polarity separate). Source of the
# verdict vocabulary: ef/personal_graph.py VERDICTS (immutable contract).
ROLE_DIRECT_OUTCOME = "direct_outcome"
ROLE_WEAK_PROXY = "weak_proxy"
ROLE_EXCLUSION = "exclusion"
ROLE_UNKNOWN = "unknown"

FEEDBACK_ROLES = {
    # verdict: (role, polarity (+1/-1), note)
    "useful": (ROLE_DIRECT_OUTCOME, +1, ""),
    "acted_on": (ROLE_DIRECT_OUTCOME, +1,
                 "strongest behavioral direct outcome"),
    "not_interested": (ROLE_DIRECT_OUTCOME, -1, ""),
    "save": (ROLE_WEAK_PROXY, +1,
             "value-signaling attention without action"),
    "investigate": (ROLE_WEAK_PROXY, +1,
                    "engagement signal; resolution lives in workflow "
                    "state, not here"),
    "more_like": (ROLE_WEAK_PROXY, +1, "relative direction only"),
    "less_like": (ROLE_WEAK_PROXY, -1, "relative direction only"),
    "known_already": (ROLE_EXCLUSION, 0,
                      "excluded from utility outcomes; retained as "
                      "redundancy evidence"),
    "wrong_inference": (ROLE_EXCLUSION, 0,
                        "premise fault (inference error), not ranking "
                        "quality"),
}
ANNOTATION_EXCLUDED_REASON = "annotated exclude_from_evaluation"

EVIDENCE_CLASS_CURATED = "blinded_curated_judgment"
EVIDENCE_CLASS_ONLINE_FEEDBACK = "observed_online_feedback"
EVIDENCE_CLASS_SYNTHETIC = "synthetic_fixture"
EVIDENCE_CLASS_UNKNOWN_PROPENSITY = "unknown_propensity_historical_impression"

PROV_VALID = "VALID"
_PROV_FAILURES = (
    "MISSING_CLAIMS", "MISSING_WHY_SURFACED", "NO_EVIDENCE_REFS",
    "UNRESOLVED_EVIDENCE_REF", "UNSUPPLIED")

PAIR_NORMAL = "NORMAL"
PAIR_DUPLICATE_ACROSS_ARMS = "DUPLICATE_ACROSS_ARMS"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class PacketBindError(Exception):
    """Packet/result artifact cannot bind without new field-level
    decisions — fail closed instead of improvising."""


class OffPolicyError(Exception):
    """Requested computation would present unknown-propensity history
    as an unbiased offline estimate, or otherwise violate the frozen
    off-policy stance."""


# ---------------------------------------------------------------------------
# Blinding (deterministic arm <-> presentation-slot assignment)
# ---------------------------------------------------------------------------


def _arm_digest(packet_id: str, arm_id: str) -> bytes:
    basis = f"{BLIND_SALT}|{packet_id}|{arm_id}".encode("utf-8")
    return hashlib.sha256(basis).digest()


def assign_slots(packet_id: str, arm_ids) -> dict:
    """Deterministic slot assignment for exactly two arms.

    Returns {"slots": {slot_index -> arm_id}, "reverse_map":
    {arm_id -> slot_token}} where slot tokens ITEM_1/ITEM_2 are what a
    judge sees. Arm identity lives only in the sidecar (the returned
    dict); the blinded packet itself carries neither arm ids nor scores.
    Stable regardless of caller-provided order.
    """
    arms = sorted(set(arm_ids))
    if len(arms) != 2:
        raise PacketBindError(
            f"slot assignment needs exactly two distinct arms, got "
            f"{sorted(arm_ids)!r}")
    ranked = sorted(arms, key=lambda a: (_arm_digest(packet_id, a), a))
    slots = {"0": ranked[0], "1": ranked[1]}
    reverse_map = {
        ranked[0]: OUTCOME_ITEM_1,
        ranked[1]: OUTCOME_ITEM_2,
    }
    return {"packet_id": packet_id, "slots": slots,
            "reverse_map": reverse_map}


def strip_to_blinded_view(item: dict) -> dict:
    """Project one full arm-item record onto what the judge may see.

    Removes score/rank/policy/experiment identifiers and any provenance
    machinery keys beyond the human-readable provenance packet fields;
    by construction the projection cannot carry them across.
    """
    forbidden = ("score", "rank_position", "ranking_policy",
                 "ranking_policy_version", "experiment_id", "propensity",
                 "policy_name", "policy_version", "arm_id")
    view_keys = ("item_id", "title", "claims", "why_surfaced",
                 "evidence_refs", "evidence_inventory_available",
                 "related_records", "similarity_diagnostic",
                 "surface_timestamp")
    out = {}
    for k in view_keys:
        if k in item and item[k] is not None:
            out[k] = item[k]
    carried = [k for k in forbidden if k in out]
    assert not carried, f"view key overlap with machinery {carried}"
    return out


def build_blinded_packet(packet_id: str, arms: dict, *,
                         recent_surfaced_items=None) -> dict:
    """Build the judge-facing packet from full arm payloads.

    arms: {arm_id -> list of full item records} with exactly two arms.
    The blinded view embeds items WITHOUT arm attribution under slot
    tokens; the sidecar needed to undo blinding is returned separately.
    """
    if len(set(arms.keys())) != 2:
        raise PacketBindError(
            f"a comparison packet binds exactly two arms, got "
            f"{sorted(arms)!r}")
    binding = assign_slots(packet_id, list(arms))
    items = {}
    for slot_idx, arm_id in binding["slots"].items():
        recs = arms[arm_id]
        if len(recs) != 1:
            raise PacketBindError(
                "v1 pairwise packets carry exactly one item per arm; "
                f"{arm_id} supplied {len(recs)}")
        items[f"ITEM_{int(slot_idx) + 1}"] = \
            strip_to_blinded_view(recs[0])
    blinded = {
        "packet_schema": "rre_v1_pairwise",
        "packet_id": packet_id,
        "marker_synthetic": any(
            bool(rec[0].get("marker_synthetic")) for rec in arms.values()),
        "recent_surfaced_items": list(recent_surfaced_items or []),
        "items": items,
        "questions": [
            {"question": PRIMARY_QUESTION,
             "instruction":
                 "Which item would the operator most regret missing?"},
            {"question": "MORE_USEFUL_NOW",
             "instruction":
                 "Which item is more useful NOW? Answer independently "
                 "of the regret question."},
        ],
    }
    return {"blinded_packet": blinded, "blind_sidecar": binding}


# ---------------------------------------------------------------------------
# Judge prompts (byte-frozen constants)
# ---------------------------------------------------------------------------

_DIM_LINES = "\n".join(
    f"- {d['name']} ({'benefit' if d['orientation'] == '+' else 'penalty'}):"
    f" {d['definition']}" for d in DIMENSIONS)

FROZEN_JUDGE_PROMPT_PAIRWISE = """You are a blinded preference judge for a single-operator personal knowledge system's future recommendation surface. You compare EXACTLY TWO candidate items, presented as ITEM_1 and ITEM_2. Arm identity, ranking scores, and any aggregate results are hidden from you and must be inferred by no one.

You answer ONE question about the pair:

QUESTION: <QUESTION_TEXT>

Decision vocabulary (choose exactly one):
- ITEM_1 or ITEM_2 — you genuinely prefer one over the other on this question.
- TIE — the two are equivalent ON THIS QUESTION; equivalent quality is a real outcome, do not force a preference.
- NEITHER — neither item merits surfacing for this operator at all.

Item fields you receive: title, claims (what the item asserts), why_surfaced (the surfacing rationale), evidence_refs (supporting material), related_records (claimed relations to the operator's Goals/Interests/InformationNeeds; may be empty). A win must rest on substance, not an attractive title: check whether claims and evidence actually support the asserted value. An item whose related_records are absent is not disqualified, but weight its claimed goal-relevance accordingly.

Recent context: RECENT_ITEMS lists items this operator has already seen lately; redundancy against them matters (see dimensions).

Dimension definitions available to you (report separately, never let one dimension swallow the choice):
<DIMENSIONS>

Return ONLY compact JSON of shape:
{"outcome": "<ITEM_1|ITEM_2|TIE|NEITHER>"}
No prose, no markdown fences.

RECENT_ITEMS: <RECENT_ITEMS>

ITEM_1:
<ITEM_1_JSON>

ITEM_2:
<ITEM_2_JSON>"""

FROZEN_JUDGE_PROMPT_DIMENSIONS = """You are a blinded rater for a single-operator personal knowledge system's future recommendation surface. You rate EXACTLY TWO candidate items (ITEM_1, ITEM_2) on fixed dimensions. You do NOT decide a preference; ratings only.

For each item and each dimension below return HIGH | MEDIUM | LOW | UNKNOWN. Use UNKNOWN when you cannot tell; do not guess. Read definitions exactly; penalty dimensions are marked — HIGH there is BAD.

Dimensions:
<DIMENSIONS>

Context notes: recent_surfaced_items are things the operator already saw lately (feeds redundancy/known_already/novelty). Judge knowledge beyond provided evidence does not count as operator knowledge.

Return ONLY compact JSON of shape:
{"ITEM_1": {"<dim>": "<HIGH|MEDIUM|LOW|UNKNOWN>", ...},
 "ITEM_2": {...}}
Exactly the nine dimension keys each. No prose, no markdown fences.

RECENT_ITEMS: <RECENT_ITEMS>

ITEM_1:
<ITEM_1_JSON>

ITEM_2:
<ITEM_2_JSON>"""


def render_dimensions_prompt(recent_items: list[str],
                             item1_json: str, item2_json: str) -> str:
    head = FROZEN_JUDGE_PROMPT_DIMENSIONS.replace(
        "<DIMENSIONS>", _DIM_LINES)
    head = head.replace(
        "<RECENT_ITEMS>",
        "; ".join(recent_items) if recent_items else "(none recorded)")
    head = head.replace("<ITEM_1_JSON>", item1_json)
    head = head.replace("<ITEM_2_JSON>", item2_json)
    return head


FROZEN_JUDGE_PROMPT_LISTWISE = """You are a blinded list-quality judge for a single-operator personal knowledge system's future recommendation surface. You compare EXACTLY TWO ranked top-k lists, LIST_1 and LIST_2, built for the same moment and the same candidate pool. Arm identity and scores are hidden.

Do two things:

1. PREFER a list: which list would you rather have been shown? Vocabulary: LIST_1 | LIST_2 | TIE (equivalent quality is real) | NEITHER (neither list merits surfacing). A list that merely repeats recently seen material or stacks weak-evidence items does not win on volume.
2. MARK regret items: within EACH list, mark every item the operator would most regret missing. Zero marks are allowed for a list; do not pad.

Item fields: title, claims, why_surfaced, evidence_refs, related_records. Judge substance against recent context; a claim without supporting evidence cannot carry a regret mark.

Recent context: RECENT_ITEMS = already-seen-lately items; redundancy against them matters.

Return ONLY compact JSON of shape:
{"outcome": "<LIST_1|LIST_2|TIE|NEITHER>",
 "regret_marks": {"LIST_1": ["<item_id>", ...], "LIST_2": [...]}}
No prose, no markdown fences.

RECENT_ITEMS: <RECENT_ITEMS>

LIST_1:
<LIST_1_JSON>

LIST_2:
<LIST_2_JSON>"""

# Register all frozen prompt hashes (byte-exact constants above).
# All frozen prompt hashes (byte-exact constants above).
FROZEN_PROMPTS_SHA256 = {
    "pairwise_regret": None,   # filled below once render_pairwise_prompt exists
    "pairwise_usefulness_now": None,
    "dimensions": sha256_bytes(FROZEN_JUDGE_PROMPT_DIMENSIONS.encode(
        "utf-8")),
    "listwise": sha256_bytes(FROZEN_JUDGE_PROMPT_LISTWISE.encode(
        "utf-8")),
}


def render_listwise_prompt(recent_items: list[str],
                           list1_json: str, list2_json: str) -> str:
    head = FROZEN_JUDGE_PROMPT_LISTWISE.replace(
        "<RECENT_ITEMS>",
        "; ".join(recent_items) if recent_items else "(none recorded)")
    head = head.replace("<LIST_1_JSON>", list1_json)
    head = head.replace("<LIST_2_JSON>", list2_json)
    return head


def build_blinded_list_packet(packet_id: str, arms: dict, *,
                              k: int = 5,
                              recent_surfaced_items=None) -> dict:
    """Judge-facing top-k list comparison packet (arm-blinded).

    arms: {arm_id -> ordered item records} exactly two; lists truncate
    to k at build time (k frozen per packet in the doc).
    """
    if len(set(arms.keys())) != 2:
        raise PacketBindError(
            f"a listwise packet binds exactly two arms, got "
            f"{sorted(arms)!r}")
    binding = assign_slots(packet_id + "|listwise", list(arms))
    # listwise presentation tokens differ from pairwise tokens
    binding["slots"] = dict(binding["slots"])
    binding["reverse_map"] = {
        arm: token.replace("ITEM_", "LIST_")
        for arm, token in binding["reverse_map"].items()}
    lists = {}
    for arm_id, token in binding["reverse_map"].items():
        recs = [strip_to_blinded_view(r) for r in arms[arm_id][:k]]
        if not recs:
            raise PacketBindError(f"empty list for arm {arm_id}")
        lists[token] = recs
    blinded = {
        "packet_schema": "rre_v1_listwise",
        "packet_id": packet_id,
        "k": k,
        "marker_synthetic": any(
            bool(r.get("marker_synthetic"))
            for recs in arms.values() for r in recs[:k]),
        "recent_surfaced_items": list(recent_surfaced_items or []),
        "lists": lists,
    }
    return {"blinded_packet": blinded, "blind_sidecar": binding}


def parse_listwise_result(raw: str) -> dict | None:
    """{"outcome": ..., "regret_marks": {...}} extraction; None on any
    malformed payload — never invents a preference."""
    import re
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out_of_vocab = j.get("outcome") not in (
        "LIST_1", "LIST_2", OUTCOME_TIE, OUTCOME_NEITHER)
    marks = j.get("regret_marks")
    valid_marks = (isinstance(marks, dict)
                   and set(marks.keys()) == {"LIST_1", "LIST_2"}
                   and all(isinstance(v, list) for v in marks.values()))
    if out_of_vocab or not valid_marks:
        return None
    return j


def overlap_at_k(list_a: list[str], list_b: list[str]) -> float | None:
    """Exact set-overlap fraction of two equal-length top-k ids."""
    ka, kb = len(list_a), len(list_b)
    if ka == 0 or kb == 0 or ka != kb:
        return None
    return len(set(list_a) & set(list_b)) / ka


def aggregate_list_results(list_records: list[dict]) -> dict:
    """Exact listwise accounting across unblinded list results.

    Each record: {packet_id, kind?, preferred_list (post-unbind arm id
    or TIE/NEITHER), regret_marks_by_arm {arm -> [item_ids]},
    list_ids_by_arm {arm -> [item_ids]}, challenger_arm, baseline_arm,
    prov_valid_all}.
    Provisional (any provenance-invalid item in either list) pairs are
    reported but excluded from gate counts.
    """
    counts = {"challenger_list_wins": 0, "baseline_list_wins": 0,
              "ties": 0, "neither": 0, "provisional_excluded": 0,
              "lists_total": 0}
    marks_challenger_total = marks_baseline_total = 0
    overlaps = []
    for rec in list_records:
        counts["lists_total"] += 1
        chal, base = rec.get("challenger_arm"), rec.get("baseline_arm")
        pref = rec.get("preferred_list")
        if not rec.get("prov_valid_all"):
            counts["provisional_excluded"] += 1
            continue
        c_ids = rec.get("list_ids_by_arm", {}).get(chal) or []
        b_ids = rec.get("list_ids_by_arm", {}).get(base) or []
        ov = overlap_at_k(c_ids, b_ids)
        if ov is not None:
            overlaps.append(ov)
        mb = (rec.get("regret_marks_by_arm") or {}).get(chal) or []
        mbase = (rec.get("regret_marks_by_arm") or {}).get(base) or []
        marks_challenger_total += len(mb)
        marks_baseline_total += len(mbase)
        if pref == chal:
            counts["challenger_list_wins"] += 1
        elif pref == base:
            counts["baseline_list_wins"] += 1
        elif pref == OUTCOME_TIE:
            counts["ties"] += 1
        elif pref == OUTCOME_NEITHER:
            counts["neither"] += 1
    denom = counts["challenger_list_wins"] + counts["baseline_list_wins"]
    return {
        "counts": counts,
        "strict_preference_rate_challenger":
            (counts["challenger_list_wins"] / denom) if denom else None,
        "regret_marked_items_challenger": marks_challenger_total,
        "regret_marked_items_baseline": marks_baseline_total,
        "mean_overlap_at_k": (sum(overlaps) / len(overlaps))
                             if overlaps else None,
        "n_overlap_comparisons": len(overlaps),
    }


def render_pairwise_prompt(question_text: str, recent_items: list[str],
                           item1_json: str, item2_json: str) -> str:
    head = FROZEN_JUDGE_PROMPT_PAIRWISE.replace(
        "<QUESTION_TEXT>", question_text)
    head = head.replace("<DIMENSIONS>", _DIM_LINES)
    head = head.replace(
        "<RECENT_ITEMS>",
        "; ".join(recent_items) if recent_items else "(none recorded)")
    head = head.replace("<ITEM_1_JSON>", item1_json)
    head = head.replace("<ITEM_2_JSON>", item2_json)
    return head


FROZEN_PROMPTS_SHA256["pairwise_regret"] = sha256_bytes(
    render_pairwise_prompt(
        "Which item would the operator most regret missing?",
        [], "{}", "{}").encode("utf-8"))
FROZEN_PROMPTS_SHA256["pairwise_usefulness_now"] = sha256_bytes(
    render_pairwise_prompt(
        "Which item is more useful NOW?",
        [], "{}", "{}").encode("utf-8"))

JUDGE_MODEL = "gpt-5.6-luna"
JUDGE_REASONING_EFFORT = "low"
JUDGE_TIMEOUT_S = 300
JUDGE_MAX_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Packet validation + provenance
# ---------------------------------------------------------------------------


def validate_item_provenance(item: dict,
                             evidence_inventory=None) -> str:
    """Mechanical provenance state, fail-closed.

    VALID requires ALL of: non-empty claims; non-empty why_surfaced;
    non-empty evidence refs; and — when an inventory of resolvable
    evidence entries is supplied — every ref resolves against it.
    Without an inventory refs are UNVERIFIABLE, which is not VALID.
    Related_records absence does NOT invalidate: attribution coverage
    is reported, not gating (symmetric across arms).
    """
    if not isinstance(item, dict):
        return "UNSUPPLIED"
    claims = (item.get("claims") or "").strip() \
        if isinstance(item.get("claims"), str) else ""
    why = (item.get("why_surfaced") or "").strip() \
        if isinstance(item.get("why_surfaced"), str) else ""
    refs = item.get("evidence_refs")
    refs = refs if isinstance(refs, list) else []
    if not claims:
        return "MISSING_CLAIMS"
    if not why:
        return "MISSING_WHY_SURFACED"
    if not refs:
        return "NO_EVIDENCE_REFS"
    if evidence_inventory is None:
        # no resolvable inventory supplied -> unverifiable, not VALID
        # (fail-closed: absence of checking infrastructure is never
        # silent success)
        return "UNRESOLVED_EVIDENCE_REF"
    missing = [r for r in refs if r not in evidence_inventory]
    if missing:
        return "UNRESOLVED_EVIDENCE_REF"
    if not evidence_inventory:
        return "UNRESOLVED_EVIDENCE_REF"
    return PROV_VALID


def validate_packet(packet_doc: dict, evidence_inventory=None) -> dict:
    """Fail-closed structural validation of a blinded packet document."""
    errors = []
    pk = packet_doc.get("blinded_packet") or packet_doc
    if pk.get("packet_schema") != "rre_v1_pairwise":
        errors.append(f"unsupported schema {pk.get('packet_schema')!r}")
    items = pk.get("items") or {}
    if set(items.keys()) != {"ITEM_1", "ITEM_2"}:
        errors.append(f"expected exactly ITEM_1+ITEM_2, got "
                      f"{sorted(items)}")
    states = {}
    for key in ("ITEM_1", "ITEM_2"):
        it = items.get(key) or {}
        states[key] = validate_item_provenance(it, evidence_inventory)
        dup = bool(it.get("item_id"))
        if not dup:
            errors.append(f"{key}: missing item_id")
        if not (it.get("marker_synthetic") is True
                or it.get("marker_synthetic") is False):
            if "marker_synthetic" not in pk and \
                    "marker_synthetic" not in it:
                errors.append(f"{key}: synthetic/provenance marker "
                              "absent")
    for token in ("score", "rank_position", "ranking_policy",
                  "experiment_id", "propensity"):
        blob = json.dumps(pk, sort_keys=True, default=str)
        if f'"{token}"' in blob:
            errors.append(f"packet leaked forbidden field {token!r}")
    return {"ok": not errors, "errors": errors,
            "provenance_states": states}


# ---------------------------------------------------------------------------
# Judging (transport injected; cached by pair hash like ISEM)
# ---------------------------------------------------------------------------


class _JudgeRecorder:
    def __init__(self, fn, log):
        self.fn, self.log = fn, log

    def __call__(self, prompt_text):
        key = sha256_bytes(prompt_text.encode("utf-8"))
        raw = self.fn(prompt_text)
        self.log.append({"prompt_sha256": key,
                         "raw": raw})
        return raw


def parse_outcome(raw: str) -> str | None:
    """Extract the outcome JSON; anything malformed returns None and
    NEVER invents a decision."""
    import re
    objs = re.findall(r"\{[^{}]*\}", raw or "")
    for o in reversed(objs):
        try:
            j = json.loads(o)
        except json.JSONDecodeError:
            continue
        val = j.get("outcome")
        if val in OUTCOME_VALUES:
            return val
    return None


def parse_dimension_ratings(raw: str) -> dict | None:
    import re
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out = {}
    for token in ("ITEM_1", "ITEM_2"):
        row = j.get(token) if isinstance(j.get(token), dict) else None
        if row is None:
            return None
        norm = {}
        for dim, rating in row.items():
            if dim not in DIMENSION_NAMES:
                continue
            r = str(rating).strip().upper()
            if r in DIMENSION_RATINGS:
                norm[dim] = r
        if len(norm) != len(DIMENSION_NAMES):
            return None
        out[token] = norm
    return out


# ---------------------------------------------------------------------------
# Results: unblinding + pair accounting
# ---------------------------------------------------------------------------


def unbind_result(blinded_packet: dict, blind_sidecar: dict,
                  judgments: dict, *, dims_ratings=None,
                  transport_meta=None) -> dict:
    """Attach judge outcomes (slot-tokenized) plus sidecar identity.

    judgments: {question -> slot outcome}. Emits canonical deblinded
    fields `regret_preferred_arm` etc.; TIE/NEITHER remain as-is.
    """
    rev = blind_sidecar["reverse_map"]
    slot_for_arm = {v: k for k, v in rev.items()}
    if set(slot_for_arm) != {"ITEM_1", "ITEM_2"}:
        raise PacketBindError("sidecar reverse map incomplete")
    out = {
        "packet_id": blinded_packet["packet_id"],
        "arms": {},
        "judgments": {},
        "dims_ratings": dims_ratings or None,
        "transport_meta": transport_meta or None,
    }
    for arm_id, token in rev.items():
        out["arms"][arm_id] = blinded_packet["items"].get(
            token, {}).get("item_id")
    for q in QUESTIONS:
        raw = judgments.get(q)
        if raw not in OUTCOME_VALUES:
            raise PacketBindError(
                f"judgment for {q} not in outcome vocabulary: {raw!r}")
        entry = {"slot_outcome": raw}
        if raw in (OUTCOME_TIE, OUTCOME_NEITHER):
            entry["preferred_arm"] = raw
        else:
            entry["preferred_arm"] = slot_for_arm[raw]
        out["judgments"][q] = entry
    return out


def pair_kind(packet_doc: dict) -> str:
    """Detect both-arms-same-underlying-item pairs."""
    pk = packet_doc.get("blinded_packet") or packet_doc
    items = pk.get("items") or {}
    i1 = (items.get("ITEM_1") or {}).get("item_id")
    i2 = (items.get("ITEM_2") or {}).get("item_id")
    if i1 and i2 and i1 == i2:
        return PAIR_DUPLICATE_ACROSS_ARMS
    return PAIR_NORMAL


def aggregate_pair_results(pair_records: list[dict]) -> dict:
    """Exact pairwise accounting per question.

    Each record carries: kind, prov_item1/prov_item2 (states),
    judgments {question -> preferred_arm}, challenger_arm,
    baseline_arm, similarity diagnostics optional per arm.
    Strict-win denominators = pairs with a strict preferred arm AND
    normal kind AND both sides provenance VALID. Provisional pairs are
    reported, never silently dropped, and never counted toward gate
    outcomes.
    """
    report = {}
    details = []
    for q in QUESTIONS:
        counts = {"A_strict_wins": 0, "B_strict_wins": 0,
                  "ties": 0, "neither": 0, "provisional_excluded": 0,
                  "duplicate_across_arms": 0, "pairs_total": 0}
        wins_by_arm: dict[str, int] = {}
        decided_strict_conformance = 0
        counter_similarity_wins_challenger = 0
        challenger_all_sim_supported = True
        sim_axis_present = False
        for rec in pair_records:
            counts["pairs_total"] += 1
            j = rec.get("judgments") or {}
            pref = (j.get(q) or {}).get("preferred_arm")
            chal = rec.get("challenger_arm")
            base = rec.get("baseline_arm")
            prov_ok = (rec.get("prov_item1") == PROV_VALID
                       and rec.get("prov_item2") == PROV_VALID)
            if rec.get("kind") == PAIR_DUPLICATE_ACROSS_ARMS:
                counts["duplicate_across_arms"] += 1
                continue
            if not prov_ok:
                # falls outside every decided denominator entirely;
                # still reported by count, never silently dropped
                counts["provisional_excluded"] += 1
                continue
            if pref in (None, OUTCOME_TIE, OUTCOME_NEITHER):
                counts["ties" if pref == OUTCOME_TIE
                       else "neither"] += 1
                continue
            winner = pref
            wins_by_arm[winner] = wins_by_arm.get(winner, 0) + 1
            decided_strict_conformance += 1
            if winner == chal:
                counts["B_strict_wins"] += 1
            elif winner == base:
                counts["A_strict_wins"] += 1
            s1 = rec.get("similarity_diagnostic", {}).get(winner) \
                if isinstance(rec.get("similarity_diagnostic"), dict) \
                else None
            loser = base if winner == chal else chal
            s2 = rec.get("similarity_diagnostic", {}).get(loser) \
                if isinstance(rec.get("similarity_diagnostic"), dict) \
                else None
            if s1 is not None and s2 is not None:
                sim_axis_present = True
                if winner == chal and s1 <= s2:
                    counter_similarity_wins_challenger += 1
                if winner == chal and s1 > s2:
                    challenger_all_sim_supported = False
        cw = counts["B_strict_wins"]
        bw = counts["A_strict_wins"]
        denom = cw + bw
        report[q] = {
            "counts": counts,
            "strict_preference_rate_challenger": (cw / denom)
                                                 if denom else None,
            "strict_preference_rate_baseline": (bw / denom)
                                               if denom else None,
            "n_decided_strict_conformance": denom,
            "wins_by_arm_exact": dict(sorted(wins_by_arm.items())),
            "counter_similarity_wins_challenger":
                counter_similarity_wins_challenger,
            "similarity_axis_tested": sim_axis_present,
        }
        details.append(q)
    return report


# ---------------------------------------------------------------------------
# Item profile quadrants (dimensions never collapse into one score)
# ---------------------------------------------------------------------------


def classify_quadrant(dims: dict) -> str:
    """Frozen precedence: redundancy > known_already > importance+novelty
    > novelty-low-value > mixed. Deterministic; UNKNOWN fails safe to
    MIXED_UNCLASSIFIED except where the rule reads the specific dim."""
    def hi(d):
        return dims.get(d) == "HIGH"

    if hi("redundancy"):
        return QUADRANT_REDUNDANT_RECENT
    if hi("known_already"):
        return QUADRANT_USEFUL_KNOWN
    consequence = dims.get("consequence")
    novelty = dims.get("novelty_to_user")
    if consequence == "HIGH" and novelty == "HIGH":
        return QUADRANT_IMPORTANT_NOVEL
    if novelty == "HIGH" and consequence == "LOW" \
            and dims.get("actionability") == "LOW":
        return QUADRANT_NOVEL_LOW_VALUE
    return QUADRANT_MIXED_UNCLASSIFIED


# ---------------------------------------------------------------------------
# Finite-set outcome + generalization status (ISEM lesson, dual output)
# ---------------------------------------------------------------------------


def finite_set_outcome(agg: dict, question: str) -> dict:
    """Exact verdict over the judged packet set for one question."""
    block = agg[question]
    c = block["counts"]
    cw, bw = c["B_strict_wins"], c["A_strict_wins"]
    denom = c["A_strict_wins"] + c["B_strict_wins"]
    decidable = (c["pairs_total"] - c["duplicate_across_arms"]
                 - c["provisional_excluded"])
    if decidable <= 0:
        status = "NOT_EVALUABLE"
    elif denom == 0:
        status = "DECISION_INERTIA"
    elif cw > bw:
        status = "CHALLENGER_PREFERRED"
    elif bw > cw:
        status = "BASELINE_PREFERRED"
    else:
        status = "DEAD_HEAT"
    return {
        "status": status,
        "exact_counts": c,
        "strict_preference_rate_challenger":
            block["strict_preference_rate_challenger"],
        "strict_preference_rate_baseline":
            block["strict_preference_rate_baseline"],
        "counter_similarity_wins_challenger":
            block["counter_similarity_wins_challenger"],
        "similarity_axis_tested": block["similarity_axis_tested"],
    }


def generalization_status(agg: dict, question: str,
                          pair_records: list[dict]) -> dict:
    """SUFFICIENT_EVIDENCE requires BOTH thresholds (frozen):
    >=5 decided strict conformance pairs and >=5 distinct packets."""
    block = agg[question]
    n_decided = block["n_decided_strict_conformance"]
    packets = {rec.get("packet_id") for rec in pair_records}
    sufficient = (n_decided >= MIN_DECIDED_PAIRS_FOR_GENERALIZATION
                  and len(packets)
                  >= MIN_DISTINCT_PACKETS_FOR_GENERALIZATION)
    return {
        "status": "SUFFICIENT_EVIDENCE" if sufficient
                  else "INSUFFICIENT_EVIDENCE",
        "n_decided_strict_conformance": n_decided,
        "threshold_decided_pairs":
            MIN_DECIDED_PAIRS_FOR_GENERALIZATION,
        "n_distinct_packets": len(packets),
        "threshold_distinct_packets":
            MIN_DISTINCT_PACKETS_FOR_GENERALIZATION,
    }


def primary_falsifier_verdict(finite: dict, gen: dict,
                              agg_question: dict) -> dict:
    """Frozen falsifier gate (regret question only).

    PASSED                     finite CHALLENGER_PREFERRED AND
                               generalization SUFFICIENT_EVIDENCE
    CHALLENGER_WINS_FINITE_SET finite win only (small-n honest state)
    SIMILARITY_CONFOUNDED_NOT_PASSING every challenger strict win sat
                               on higher similarity diagnostic than its
                               opponent — semantic similarity alone
                               does NOT pass
    BASELINE_PREFERRED / DEAD_HEAT / DECISION_INERTIA / NOT_EVALUABLE
                               mirrored finite statuses
    """
    status = finite["status"]
    if agg_question["similarity_axis_tested"]:
        cw = agg_question["counts"]["B_strict_wins"]
        if (status == "CHALLENGER_PREFERRED" and cw > 0
                and agg_question["counter_similarity_wins_challenger"]
                == 0):
            status = "SIMILARITY_CONFOUNDED_NOT_PASSING"
    verdict = {
        "CHALLENGER_PREFERRED": "CHALLENGER_WINS_FINITE_SET",
        "BASELINE_PREFERRED": "BASELINE_PREFERRED",
        "DEAD_HEAT": "DEAD_HEAT",
        "DECISION_INERTIA": "DECISION_INERTIA",
        "NOT_EVALUABLE": "NOT_EVALUABLE",
        "SIMILARITY_CONFOUNDED_NOT_PASSING":
            "SIMILARITY_CONFOUNDED_NOT_PASSING",
    }[status]
    if verdict == "CHALLENGER_WINS_FINITE_SET" \
            and gen["status"] == "SUFFICIENT_EVIDENCE":
        verdict = "PASSED_PRIMARY_FALSIFIER"
    return {
        "verdict": verdict,
        "finite_set_status": finite["status"],
        "generalization_status": gen["status"],
        "untested_axes": ([] if agg_question["similarity_axis_tested"]
                          else ["semantic_similarity_diagnostic"]),
    }


# ---------------------------------------------------------------------------
# Feedback roles + off-policy honesty helpers
# ---------------------------------------------------------------------------


def classify_feedback_event(event: dict) -> dict:
    """Map one impression-linked feedback event to its frozen role.

    Annotated exclusions override everything. Unknown verdicts fail
    closed to ROLE_UNKNOWN.
    """
    verdict = event.get("verdict")
    role = FEEDBACK_ROLES.get(verdict, (ROLE_UNKNOWN, 0,
                                        "unlisted verdict"))[0]
    excluded = False
    reason = FEEDBACK_ROLES.get(verdict, (None, 0,
                                          "unlisted verdict"))[2]
    anns = event.get("annotations") or []
    for a in anns:
        if a.get("exclude_from_evaluation"):
            excluded = True
            role = ROLE_EXCLUSION
            reason = ANNOTATION_EXCLUDED_REASON
            break
    return {"role": role, "excluded": excluded, "reason": reason,
            "evidence_class": EVIDENCE_CLASS_UNKNOWN_PROPENSITY
            if event.get("impression_id") else EVIDENCE_CLASS_ONLINE_FEEDBACK}


def feedback_role_summary(events: list[dict]) -> dict:
    """Counts only. Historical impressions carry propensity UNKNOWN;
    these numbers may never seed an unbiased ranking estimate."""
    summary = {}
    for ev in events:
        cls = classify_feedback_event(ev)
        key = cls["role"]
        summary[key] = summary.get(key, 0) + 1
    return {
        "roles": summary,
        "propensity": "UNKNOWN",
        "note": "sparse/non-random history; counts are descriptive "
                "only and never an unbiased offline ranking estimate",
        "evidence_class": EVIDENCE_CLASS_UNKNOWN_PROPENSITY,
    }


def refuse_offline_reward_estimate(*_args, **_kwargs):
    raise OffPolicyError(
        "RRE v1 computes no reward/utility estimate from history; "
        "unknown-propensity feedback supports descriptive counts only.")


# ---------------------------------------------------------------------------
# Full evaluation assembly
# ---------------------------------------------------------------------------


def evaluate(pair_records: list[dict], *,
             challenger_arm: str = "B", baseline_arm: str = "A",
             feedback_events=None) -> dict:
    """Assemble the complete RRE v1 report (dual-output, falsifier)."""
    agg = aggregate_pair_results(pair_records)
    per_question = {}
    for q in QUESTIONS:
        fin = finite_set_outcome(agg, q)
        gen = generalization_status(agg, q, pair_records)
        entry = {
            "finite_set_outcome": fin,
            "generalization_status": gen,
            "provisional_pairs_excluded":
                agg[q]["counts"]["provisional_excluded"],
            "duplicate_across_arms":
                agg[q]["counts"]["duplicate_across_arms"],
        }
        if q == PRIMARY_QUESTION:
            entry["primary_falsifier"] = primary_falsifier_verdict(
                fin, gen, agg[q])
        per_question[q] = entry
    overall = per_question[PRIMARY_QUESTION]["primary_falsifier"]
    feedback_block = None
    if feedback_events:
        feedback_block = feedback_role_summary(feedback_events)
    return {
        "evaluator": EVALUATOR_ID,
        "generated": time.strftime("%Y-%m-%dT%H%M%S"),
        "arms": {"challenger": challenger_arm,
                 "baseline": baseline_arm},
        "per_question": per_question,
        "primary_falsifier_verdict": overall["verdict"],
        "untested_axes": overall["untested_axes"],
        "feedback_roles_descriptive_only": feedback_block,
        "off_policy_stance": (
            "historical impressions carry propensity=UNKNOWN; no "
            "offline unbiased ranking estimate is computable here"),
        "no_training_no_ranking_claims": True,
    }


# ---------------------------------------------------------------------------
# Synthetic fixtures (fully fictional; clearly marked)
# ---------------------------------------------------------------------------


FIXTURE_INVENTORY = {
    "ev_fixture_rust_async": "fictional transcript excerpt on async "
                             "runtime internals (synthetic)",
    "ev_fixture_city_budget": "fictional municipal budget article "
                              "(synthetic)",
}

def fixture_items():
    """Four-quadrant stylized packet fixtures, synthetic-only markers."""
    base = lambda iid, title, claims, why, refs, rels, sim: {
        "item_id": iid, "title": title, "claims": claims,
        "why_surfaced": why, "evidence_refs": refs,
        "related_records": rels, "similarity_diagnostic": sim,
        "marker_synthetic": True,
    }
    important_novel = base(
        "fixture_video_alpha", "Alpha protocol CVE affects our stack",
        "Fictional: CVE-XXXX-0001 disclosed for the exact runtime "
        "version pinned in the operator's project; patch released.",
        "matches active Goal 'ship ys 1.0' via related security need",
        ["ev_fixture_rust_async"],
        [{"kind": "goal", "id": "g_fixture_ship"}], 0.31)
    useful_known = base(
        "fixture_video_beta", "Git rebase workflow explainer",
        "Fictional: restates interactive rebase patterns the operator "
        "has demonstrably used before.",
        "similarity to interest 'version control tooling'",
        ["ev_fixture_rust_async"], [],
        0.72)
    novel_low_value = base(
        "fixture_video_gamma", "Underwater rugby championship recap",
        "Fictional: game the operator has never encountered; no "
        "stated connection to goals.",
        "novelty burst detector",
        ["ev_fixture_city_budget"], [], 0.02)
    redundant_recent = base(
        "fixture_video_delta", "Same alpha CVE story, reshaped",
        "Fictional: repeats yesterday's surfaced CVE briefing with new "
        "thumbnail; no added claims.",
        "recent-duplicate re-surface",
        ["ev_fixture_rust_async"],
        [{"kind": "interest", "id": "i_fixture_security"}], 0.33)
    return [important_novel, useful_known, novel_low_value,
            redundant_recent]


def make_demo_pair_record(packet_id: str, item_a: dict, item_b: dict,
                          sidecar: dict, judgments: dict,
                          evidence_inventory=None,
                          baseline_arm="A", challenger_arm="B",
                          extra=None) -> dict:
    """Helper used by tests/demo: validate, unbind, produce a pair
    record consumable by evaluate()."""
    pkg = build_blinded_packet(packet_id,
                               {baseline_arm: [item_a],
                                challenger_arm: [item_b]})
    val = validate_packet(pkg["blinded_packet"], evidence_inventory)
    if not val["ok"]:
        raise PacketBindError(f"demo packet invalid: {val['errors']}")
    rec = unbind_result(pkg["blinded_packet"], pkg["blind_sidecar"],
                        judgments)
    rec.update({
        "kind": pair_kind(pkg["blinded_packet"]),
        "prov_item1": val["provenance_states"]["ITEM_1"],
        "prov_item2": val["provenance_states"]["ITEM_2"],
        "packet_id": packet_id,
        "challenger_arm": challenger_arm,
        "baseline_arm": baseline_arm,
        "similarity_diagnostic": {
            baseline_arm: item_a.get("similarity_diagnostic"),
            challenger_arm: item_b.get("similarity_diagnostic")},
    })
    rec.update(extra or {})
    return rec


# ---------------------------------------------------------------------------
# Freeze manifest helpers
# ---------------------------------------------------------------------------

FROZEN_ARTIFACT_PATHS = (
    "ef/eval_recommendation_regret.py",
    "scripts/eval_recommendation_regret.py",
    "tests/test_eval_recommendation_regret.py",
    "docs/handoffs/interest-intelligence/"
    "recommendation-regret-evaluator-v1/"
    "METRIC_PLAN_PREREGISTRATION.md",
)


def verify_manifest(repo_root: Path, receipt_path: Path) -> list[str]:
    """Reproduce FREEZE_RECEIPT hashes; return fatal drift strings."""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    drift = []
    for art in receipt.get("frozen_artifacts", []):
        p = repo_root / art["path"]
        if not p.exists():
            drift.append(f"missing frozen artifact {art['path']}")
            continue
        got = sha256_file(p)
        if got != art["sha256"]:
            drift.append(f"hash drift {art['path']}: expected "
                         f"{art['sha256'][:12]}… found {got[:12]}…")
    return drift
