"""Recommendation regret evaluator (RRE v1, amendment 1) — frozen core.

Amended by ARCHITECT_AMENDMENT_1_REVIEW_REPAIR (2026-08-27) after the
independent freeze review REJECTED the v1 candidate
(REVIEW-freeze-20260827.md, commit 732e2cfa5). The v1 bytes are
preserved verbatim under rejected-candidate-v1/ next to this file.

Scope of this evaluator, unchanged in spirit: it freezes HOW we will
later tell whether a ranking challenger improves recommendations. It is
an evaluator ONLY: it never ranks, scores, trains, or optimizes a
policy, and it contains no utility-weight arithmetic anywhere by design.

What amendment 1 changes (each maps to a review defect):

  R1  The v1 generalization-status companion ("sufficient evidence"
      at n>=5) is REMOVED. n>=5 never licenses population-level
      language. The only companion output is DIAGNOSTIC_COVERAGE_STATUS
      (MINIMUM_DIAGNOSTIC_FLOOR_MET / MINIMUM_DIAGNOSTIC_FLOOR_NOT_MET):
      a non-inferential bookkeeping label over the judged set. Any
      population-level claim requires a separate, preregistered
      statistical design that does not exist here.
  R2  The primary falsifier fails closed on the similarity axis: with
      similarity diagnostics absent the axis is UNTESTED and the
      falsifier CANNOT pass. A counter-similarity win remains a required
      conjunct. similarity_diagnostic is evaluator-side metadata only
      and never enters a judge payload.
  R3  The judge view is ALLOWLIST-based: the judge payload is built only
      from fields the judge may see; no pass-through projection of
      arbitrary source dictionaries. Validation recursively scans the
      whole packet document for forbidden machinery keys, and the CLI
      judge path validates before rendering or invoking any judge.
  R5  Sidecars are mechanically bound to their packet (packet_id +
      canonical blinded-packet hash + arm/slot map + binding version).
      Unblinding is mechanical for pairwise AND listwise; a mismatched
      sidecar fails closed.
  R6  Every emitted report carries its evidence class; the judgments
      evidence class is a required evaluate() argument.
  R7  Mechanical counting integrity: duplicate packet ids deduplicate
      (exact duplicates) or fail closed (conflicting content);
      provisional/unprovenanced packets can never satisfy the
      diagnostic floor; the distinct-packet count uses only eligible
      decided packets; listwise regret marks are validated sets of
      candidate ids, not raw rows.

Adopts the ISEM v1 lesson: every evaluation returns BOTH
  A. FINITE_SET_OUTCOME — exact deterministic accounting over the
     packets actually judged (unchanged semantics); and
  B. DIAGNOSTIC_COVERAGE_STATUS — whether the judged set meets the
     frozen minimum diagnostic floor (non-inferential).
PASSED_PRIMARY_FALSIFIER, when emitted, is a statement about the frozen
finite evaluation set ONLY: finite-set evidence against the
similarity+recency baseline. It is NOT a population-superiority,
generalization, or production-promotion claim.

Contamination boundaries honored at design time (unchanged):
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
import hmac
import json
import secrets
import time
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

EVALUATOR_ID = "rre_v1"
AMENDMENT_ID = "ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING"
BLIND_SALT = "rre_v1_slot_blind_salt_2026_08_27"
SIDECAR_BINDING_VERSION = "rre_v3_sidecar_binding_3"

QUESTIONS = ("WOULD_REGRET_MISSING", "MORE_USEFUL_NOW")
PRIMARY_QUESTION = "WOULD_REGRET_MISSING"

# Presentation-slot outcome vocabulary for BOTH questions.
OUTCOME_ITEM_1 = "ITEM_1"
OUTCOME_ITEM_2 = "ITEM_2"
OUTCOME_TIE = "TIE"
OUTCOME_NEITHER = "NEITHER"
OUTCOME_VALUES = (OUTCOME_ITEM_1, OUTCOME_ITEM_2, OUTCOME_TIE,
                  OUTCOME_NEITHER)

LIST_OUTCOME_VALUES = ("LIST_1", "LIST_2", OUTCOME_TIE, OUTCOME_NEITHER)

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

# R1: thresholds renamed — they gate a non-inferential diagnostic floor,
# never "generalization".
MIN_DECIDED_PAIRS_FOR_DIAGNOSTIC_FLOOR = 5
MIN_DISTINCT_DECIDED_PACKETS_FOR_DIAGNOSTIC_FLOOR = 5
DIAGNOSTIC_FLOOR_MET = "MINIMUM_DIAGNOSTIC_FLOOR_MET"
DIAGNOSTIC_FLOOR_NOT_MET = "MINIMUM_DIAGNOSTIC_FLOOR_NOT_MET"

# R2: the similarity diagnostic is evaluator-side metadata only.
SIMILARITY_CONFOUNDED_DELTA_MAX = None  # per-pair decision; no threshold.

# R3 (amendment 2): judge view allowlist — the ONLY item fields a judge
# may see, and the ONLY keys a judge-visible structured container
# (related_records) may carry.
JUDGE_ITEM_VIEW_FIELDS = ("item_id", "title", "claims", "why_surfaced",
                          "evidence_refs", "related_records")
RELATED_RECORD_SCHEMA_FIELDS = ("kind", "id")

# R3 (amendment 2): machinery-equivalent key classification. Keys are
# NORMALIZED (Unicode NFKC, invisible-character strip, separator fold,
# casefold) before classification, so "ARM", "arm-id", "arm.id",
# fullwidth "ａｒｍ" and zero-width-padded variants all classify the same
# as "arm". Classification = exact normalized equivalence OR normalized
# substring. This is defense-in-depth: the primary protection is the
# positive allowlist (item views + related_records schema), not
# blacklist growth.
_MACHINERY_KEY_EQUIVALENCES = frozenset({
    "arm", "armid", "treatment", "variant", "condition", "bucket",
    "policy", "policyname", "policyversion", "rankingpolicy",
    "rankingpolicyversion", "score", "rawscore", "rawrankingscore",
    "rank", "rankposition", "experiment", "experimentid", "propensity",
    "similaritydiagnostic", "sidecar", "blindsalt", "blindslotmap",
    "blindedpackethash", "mapping", "blindmap",
})
_MACHINERY_KEY_SUBSTRINGS = ("score", "rank", "propensity", "policy",
                             "experiment", "sidecar", "arm", "treatment",
                             "variant", "condition", "bucket",
                             "diagnostic", "blind", "mapping")
_STRUCTURAL_KEY_EXEMPTIONS = frozenset({
    "blindedpacket", "packetschema", "packetid", "markersynthetic",
    "recentsurfaceditems", "itemid", "title", "claims", "whysurfaced",
    "evidencerefs", "relatedrecords", "questions", "question",
    "instruction", "kind", "id", "lists", "k",
})

_INVISIBLE_CHARS_RE = __import__("re").compile(
    "[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064"
    "\ufeff\u00ad\u180e\ufe00-\ufe0f\u115f\u1160\u3164]+")
# small-capital and single-letter homoglyph folds beyond NFKC (bounded
# table; exotic homoglyphs in curator-authored wrapper keys are a
# documented residual, caught inside judge containers by allowlists)
_SMALLCAP_FOLD = str.maketrans({
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f",
    "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k", "ʟ": "l",
    "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ʀ": "r", "ꜱ": "s",
    "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
    "ɡ": "g", "𝐚": "a", "𝐛": "b", "𝐜": "c", "𝐫": "r", "𝐦": "m",
})
_SEPARATOR_CHARS_RE = __import__("re").compile(r"[\s\-_.]+")


def normalize_key(key) -> str:
    """Amendment-2 normalized key form: NFKC, invisible-character
    strip, small-capital/homoglyph fold, separator fold, casefold."""
    s = unicodedata.normalize("NFKC", str(key))
    s = _INVISIBLE_CHARS_RE.sub("", s)
    s = s.translate(_SMALLCAP_FOLD)
    s = _SEPARATOR_CHARS_RE.sub("", s)
    return s.casefold()

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
# R6: evidence classes a judgments-derived report may carry (feedback
# events carry their own classes; judge-derived outcomes are curated
# judgments, offline demonstrations are synthetic).
JUDGMENT_EVIDENCE_CLASSES = (EVIDENCE_CLASS_CURATED,
                             EVIDENCE_CLASS_SYNTHETIC)

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
    judge sees. Arm identity lives only in the sidecar; the blinded
    packet itself carries neither arm ids nor scores. Stable regardless
    of caller-provided order.
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


def canonical_doc_hash(doc) -> str:
    """Content hash of a JSON-able document (sorted keys, compact)."""
    blob = json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return sha256_bytes(blob.encode("utf-8"))


def _require_packet_id(packet_id) -> str:
    """Amendment-2 hygiene: packet_id is required and validated."""
    if not isinstance(packet_id, str) or not packet_id.strip():
        raise PacketBindError(
            f"packet_id must be a non-empty string, got {packet_id!r}")
    return packet_id


def canonical_binding(packet_id: str, arms, schema: str) -> dict:
    """Single source of truth for the arm->slot assignment (R5).

    Used by BOTH packet construction and unblinding, so the sidecar map
    can be re-derived from the frozen assignment function + frozen salt
    and compared exactly — a stored reverse_map is never trusted on its
    own.
    """
    salted = packet_id + ("|listwise"
                          if schema == "rre_v1_listwise" else "")
    binding = assign_slots(salted, list(arms))
    if schema == "rre_v1_listwise":
        reverse_map = {arm: token.replace("ITEM_", "LIST_")
                       for arm, token in binding["reverse_map"].items()}
    else:
        reverse_map = dict(binding["reverse_map"])
    return {"packet_id": packet_id,
            "arms": sorted(set(arms)),
            "slots": dict(binding["slots"]),
            "reverse_map": reverse_map}


def _binding_digest(blinded_packet: dict, arms, reverse_map: dict) -> str:
    """Authenticate the packet TOGETHER WITH its arm set and slot map —
    relabeling arms or flipping the map changes the digest."""
    return canonical_doc_hash({
        "blinded_packet": blinded_packet,
        "arms": list(arms),
        "reverse_map": dict(reverse_map),
    })


def _normalize_secret(binding_secret) -> bytes:
    """Binding secret: non-empty hex string or raw bytes, minimum 16
    bytes of material. The secret is generated at blind time, returned
    OUT OF BAND (never stored beside the packet or sidecar), and
    required at every unbind."""
    if isinstance(binding_secret, str):
        try:
            raw = bytes.fromhex(binding_secret)
        except ValueError:
            raise PacketBindError(
                "binding secret must be a hex string or bytes")
    elif isinstance(binding_secret, (bytes, bytearray)):
        raw = bytes(binding_secret)
    else:
        raise PacketBindError(
            "binding secret must be a hex string or bytes")
    if len(raw) < 16:
        raise PacketBindError(
            "binding secret too short (>= 16 bytes of material "
            "required)")
    return raw


def new_binding_secret() -> str:
    """Generate a fresh 256-bit binding secret (hex). Blind-time only:
    the caller stores it out of band, alongside neither the packet nor
    the sidecar."""
    return secrets.token_hex(32)


def _binding_mac(secret: bytes, blinded_packet: dict, arms,
                 reverse_map: dict) -> str:
    blob = canonical_doc_hash({
        "blinded_packet": blinded_packet,
        "arms": list(arms),
        "reverse_map": dict(reverse_map),
        "binding_version": SIDECAR_BINDING_VERSION,
    }).encode("utf-8")
    return hmac.new(secret, blob, "sha256").hexdigest()


def _opaque_id(packet_id: str, kind: str, real) -> str:
    """Amendment-2 free-text channel: evaluator-owned opaque tokens
    replace producer-controlled IDs in judge-visible positions. The
    token is derived per-packet from the real identity (never from the
    arm), so the same real item maps to the same opaque token in both
    arms and no arm correlation can leak through an ID. Non-string real
    ids fail closed instead of being silently coerced."""
    if not isinstance(real, str) or not real:
        raise PacketBindError(
            f"opaque id source ({kind}) must be a non-empty string, "
            f"got {real!r}")
    return "RRE_" + kind.upper() + "_" + sha256_bytes(
        f"{BLIND_SALT}|{packet_id}|{kind}|{real}".encode(
            "utf-8"))[:16]


def _bound_sidecar(binding: dict, blinded_packet: dict,
                   item_ids=None, secret: bytes = b"") -> dict:
    """R5: seal arm identity to THIS packet — packet_id, arm set, slot
    map, a digest binding packet+arms+map, AND an HMAC over all of it
    keyed by the blind-time secret. The secret travels out of band;
    without it, no forged or relabeled sidecar can reproduce the MAC."""
    sc = {
        "packet_id": binding["packet_id"],
        "arms": list(binding["arms"]),
        "slots": dict(binding["slots"]),
        "reverse_map": dict(binding["reverse_map"]),
        "blinded_packet_sha256": _binding_digest(
            blinded_packet, binding["arms"], binding["reverse_map"]),
        "binding_version": SIDECAR_BINDING_VERSION,
        "binding_mac": _binding_mac(secret, blinded_packet,
                                    binding["arms"],
                                    binding["reverse_map"]),
    }
    if item_ids:
        sc["item_ids"] = item_ids
    return sc


def build_judge_item_view(item: dict, packet_id: str) -> dict:
    """R3: construct the judge-visible payload from the ALLOWLIST only.

    No pass-through projection of arbitrary source dictionaries: fields
    outside JUDGE_ITEM_VIEW_FIELDS (including similarity diagnostics,
    scores, ranks, policy/experiment metadata, and any nested variants)
    cannot appear by construction. Amendment-2: the judge-visible
    item_id is an evaluator-owned opaque token (producer IDs never
    render), and related_records entries are reduced to the positive
    schema {"kind", "id"} with opaque ids — unknown structured keys fail
    closed.
    """
    if not isinstance(item, dict):
        raise PacketBindError("judge item view requires a dict record")
    view = {}
    for k in JUDGE_ITEM_VIEW_FIELDS:
        v = item.get(k)
        if v is None:
            continue
        if k in ("claims", "why_surfaced", "title") \
                and not isinstance(v, str):
            raise PacketBindError(f"judge field {k!r} must be a string")
        if k in ("evidence_refs", "related_records") \
                and not isinstance(v, list):
            raise PacketBindError(f"judge field {k!r} must be a list")
        if k == "item_id":
            view[k] = _opaque_id(_require_packet_id(packet_id), "j", v)
        elif k == "related_records":
            reduced = []
            for entry in v:
                if not isinstance(entry, dict):
                    raise PacketBindError(
                        "related_records entries must be objects with "
                        f"keys {RELATED_RECORD_SCHEMA_FIELDS}, got "
                        f"{type(entry).__name__}")
                extra = set(entry) - set(RELATED_RECORD_SCHEMA_FIELDS)
                if extra:
                    raise PacketBindError(
                        "related_records keys outside the positive "
                        f"schema {RELATED_RECORD_SCHEMA_FIELDS}: "
                        f"{sorted(extra)}")
                kind, rid = entry.get("kind"), entry.get("id")
                if not isinstance(kind, str) or not kind \
                        or not isinstance(rid, str) or not rid:
                    raise PacketBindError(
                        "related_records entries require non-empty "
                        "string 'kind' and 'id'")
                reduced.append({
                    "kind": kind,
                    "id": _opaque_id(_require_packet_id(packet_id),
                                     "ref", rid)})
            view[k] = reduced
        else:
            view[k] = v
    if not view.get("item_id"):
        raise PacketBindError("judge item view requires item_id")
    return view


def build_blinded_packet(packet_id: str, arms: dict, *,
                         recent_surfaced_items=None,
                         binding_secret=None) -> dict:
    """Build the judge-facing packet from full arm payloads.

    arms: {arm_id -> list of full item records} with exactly two arms.
    The blinded view embeds allowlisted item views WITHOUT arm
    attribution under slot tokens; the sidecar needed to undo blinding
    is returned separately, HMAC-authenticated with a blind-time
    binding secret. The secret is returned under "binding_secret" and
    must be stored OUT OF BAND — beside neither the packet nor the
    sidecar; every unbind requires it (amendment 2, R5).
    """
    _require_packet_id(packet_id)
    if len(set(arms.keys())) != 2:
        raise PacketBindError(
            f"a comparison packet binds exactly two arms, got "
            f"{sorted(arms)!r}")
    secret_hex = binding_secret or new_binding_secret()
    secret = _normalize_secret(secret_hex)
    binding = canonical_binding(packet_id, list(arms), "rre_v1_pairwise")
    items = {}
    item_ids = {}
    for slot_idx, arm_id in binding["slots"].items():
        recs = arms[arm_id]
        if len(recs) != 1:
            raise PacketBindError(
                "v1 pairwise packets carry exactly one item per arm; "
                f"{arm_id} supplied {len(recs)}")
        view = build_judge_item_view(recs[0], packet_id)
        items[f"ITEM_{int(slot_idx) + 1}"] = view
        item_ids[arm_id] = {"judge_id": view["item_id"],
                            "real_id": recs[0].get("item_id")}
    blinded = {
        "packet_schema": "rre_v1_pairwise",
        "packet_id": packet_id,
        "marker_synthetic": any(
            bool(rec[0].get("marker_synthetic")) for rec in arms.values()),
        "recent_surfaced_items": [
            _opaque_id(packet_id, "r", r)
            for r in list(recent_surfaced_items or [])],
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
    return {"blinded_packet": blinded,
            "blind_sidecar": _bound_sidecar(binding, blinded, item_ids,
                                            secret),
            "binding_secret": secret_hex}


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
                              recent_surfaced_items=None,
                              binding_secret=None) -> dict:
    """Judge-facing top-k list comparison packet (arm-blinded, bound).

    arms: {arm_id -> ordered item records} exactly two; lists truncate
    to k at build time (k frozen per packet in the doc). Items enter
    the lists through the same judge-view allowlist and opaque-ID
    scheme as pairwise; the sidecar re-derivation + HMAC binding covers
    the listwise path identically (amendment 2, R5).
    """
    _require_packet_id(packet_id)
    if len(set(arms.keys())) != 2:
        raise PacketBindError(
            f"a listwise packet binds exactly two arms, got "
            f"{sorted(arms)!r}")
    secret_hex = binding_secret or new_binding_secret()
    secret = _normalize_secret(secret_hex)
    binding = canonical_binding(packet_id, list(arms), "rre_v1_listwise")
    lists = {}
    item_ids = {}
    for arm_id, token in binding["reverse_map"].items():
        recs = [build_judge_item_view(r, packet_id)
                for r in arms[arm_id][:k]]
        if not recs:
            raise PacketBindError(f"empty list for arm {arm_id}")
        lists[token] = recs
        item_ids[arm_id] = [{"judge_id": view["item_id"],
                             "real_id": real.get("item_id")}
                            for view, real in zip(recs,
                                                  arms[arm_id][:k])]
    blinded = {
        "packet_schema": "rre_v1_listwise",
        "packet_id": packet_id,
        "k": k,
        "marker_synthetic": any(
            bool(r.get("marker_synthetic"))
            for recs in arms.values() for r in recs[:k]),
        "recent_surfaced_items": [
            _opaque_id(packet_id, "r", r)
            for r in list(recent_surfaced_items or [])],
        "lists": lists,
    }
    return {"blinded_packet": blinded,
            "blind_sidecar": _bound_sidecar(binding, blinded, item_ids,
                                            secret),
            "binding_secret": secret_hex}


def parse_outcome(raw: str) -> str | None:
    """Extract THE outcome; anything malformed yields None and NEVER
    invents a decision. Contradictory in-vocabulary tokens (e.g. a
    decoy JSON emitted after the final answer) also yield None — no
    last-wins selection. A single token may repeat safely."""
    import re
    values = []
    for o in re.findall(r"\{[^{}]*\}", raw or ""):
        try:
            j = json.loads(o)
        except json.JSONDecodeError:
            continue
        v = j.get("outcome")
        if v in OUTCOME_VALUES:
            values.append(v)
    if len(set(values)) == 1:
        return values[0]
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
    out_of_vocab = j.get("outcome") not in LIST_OUTCOME_VALUES
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

    Each record: {packet_id, preferred_list (post-unbind arm id or
    TIE/NEITHER), regret_marks_by_arm {arm -> [item_ids]},
    list_ids_by_arm {arm -> [item_ids]}, challenger_arm, baseline_arm,
    prov_valid_all}. Records should come from unbind_list_result, which
    validates marks as sets of candidate ids; totals deduplicate
    defensively. Provisional (any provenance-invalid item in either
    list) pairs are reported but excluded from gate counts; duplicate
    packet ids deduplicate when content-identical and fail closed
    otherwise.
    """
    counts = {"challenger_list_wins": 0, "baseline_list_wins": 0,
              "ties": 0, "neither": 0, "provisional_excluded": 0,
              "duplicate_records_deduplicated": 0, "lists_total": 0}
    marks_challenger_total = marks_baseline_total = 0
    overlaps = []
    seen: dict[str, str] = {}
    for rec in list_records:
        counts["lists_total"] += 1
        pid = rec.get("packet_id")
        fp = canonical_doc_hash(rec)
        if pid is not None:
            if pid in seen:
                if seen[pid] != fp:
                    raise PacketBindError(
                        f"conflicting duplicate listwise packet_id "
                        f"{pid!r}")
                counts["duplicate_records_deduplicated"] += 1
                continue
            seen[pid] = fp
        chal, base = rec.get("challenger_arm"), rec.get("baseline_arm")
        pref = rec.get("preferred_list")
        if pref is None:
            raise PacketBindError(
                f"missing preferred_list judgment in listwise packet "
                f"{rec.get('packet_id')!r}")
        if pref not in (OUTCOME_TIE, OUTCOME_NEITHER, chal, base):
            raise PacketBindError(
                f"unknown preferred_list {pref!r} for listwise packet "
                f"{rec.get('packet_id')!r} (arms {chal!r}/{base!r})")
        if not rec.get("prov_valid_all"):
            counts["provisional_excluded"] += 1
            continue
        c_ids = rec.get("list_ids_by_arm", {}).get(chal) or []
        b_ids = rec.get("list_ids_by_arm", {}).get(base) or []
        ov = overlap_at_k(c_ids, b_ids)
        if ov is not None:
            overlaps.append(ov)
        mb = _dedup((rec.get("regret_marks_by_arm") or {}).get(chal))
        mbase = _dedup((rec.get("regret_marks_by_arm") or {}).get(base))
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


def _dedup(ids) -> list:
    """Order-preserving dedup of a mark/id list."""
    out, seen = [], set()
    for i in ids or []:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


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


def _recursive_machinery_keys(doc, path="") -> list[str]:
    """R3 (amendment 2): find machinery-equivalent keys at ANY depth.

    Keys are classified after normalization (NFKC, invisible-character
    strip, separator fold, casefold): exact normalized equivalence with
    a machinery class, or a normalized machinery substring — unless the
    normalized key is a sanctioned structural key."""
    found = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            here = f"{path}.{k}" if path else str(k)
            norm = normalize_key(k)
            if norm in _MACHINERY_KEY_EQUIVALENCES or (
                    norm not in _STRUCTURAL_KEY_EXEMPTIONS
                    and any(s in norm
                            for s in _MACHINERY_KEY_SUBSTRINGS)):
                found.append(here)
            found.extend(_recursive_machinery_keys(v, here))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            found.extend(_recursive_machinery_keys(v, f"{path}[{i}]"))
    return found


def validate_packet(packet_doc: dict, evidence_inventory=None) -> dict:
    """Fail-closed structural validation of a blinded packet document.

    Defense in depth, allowlist-first (R3):
      1. every judge item view — pairwise AND listwise — may contain
         ONLY JUDGE_ITEM_VIEW_FIELDS, with controlled type checks;
      2. related_records entries must match the positive schema
         {"kind", "id"} — unknown structured keys fail closed;
      3. the ENTIRE document (wrapper included) is recursively scanned
         for machinery-equivalent keys (normalized classification);
      4. schema/shape/marker checks as in v1; packet_id required.
    Provenance states are informational and fail closed to NOT VALID
    whenever evidence cannot be resolved. Identity fields in a
    judge-facing document are evaluator-owned opaque tokens by
    construction (amendment 2); evidence_refs remain curator-supplied
    strings under the documented curator rules.
    """
    errors = []
    if not isinstance(packet_doc, dict):
        return {"ok": False, "errors": ["packet document must be a "
                                        "JSON object"],
                "provenance_states": {},
                "inventory_supplied": evidence_inventory is not None}
    pk = packet_doc.get("blinded_packet") or packet_doc
    if not isinstance(pk, dict):
        return {"ok": False, "errors": ["blinded_packet must be a JSON "
                                        "object"],
                "provenance_states": {},
                "inventory_supplied": evidence_inventory is not None}
    schema = pk.get("packet_schema")
    if schema not in ("rre_v1_pairwise", "rre_v1_listwise"):
        errors.append(f"unsupported schema {schema!r}")
        return {"ok": False, "errors": errors,
                "provenance_states": {},
                "inventory_supplied": evidence_inventory is not None}
    pid = pk.get("packet_id")
    if not isinstance(pid, str) or not pid.strip():
        errors.append(f"packet_id must be a non-empty string, got "
                      f"{pid!r}")
    # 1. allowlist + controlled types per judge-visible view — pairwise
    # items AND listwise list entries alike (R3: no bypass path)
    if schema == "rre_v1_pairwise":
        raw_views = pk.get("items")
        if raw_views is not None and not isinstance(raw_views, dict):
            errors.append("items must be a JSON object")
            raw_views = {}
        views = dict(raw_views or {})
    else:
        raw_lists = pk.get("lists")
        if raw_lists is not None and not isinstance(raw_lists, dict):
            errors.append("lists must be a JSON object")
            raw_lists = {}
        views = {}
        for tok, lst in (raw_lists or {}).items():
            if not isinstance(lst, list):
                errors.append(f"{tok}: list entries must be a JSON "
                              "array")
                continue
            for i, v in enumerate(lst):
                views[f"{tok}[{i}]"] = v
    for key, view in sorted(views.items()):
        if not isinstance(view, dict):
            errors.append(f"{key}: judge view must be a JSON object")
            continue
        extra = set(view) - set(JUDGE_ITEM_VIEW_FIELDS)
        if extra:
            errors.append(
                f"{key}: judge-view fields outside the allowlist: "
                f"{sorted(extra)}")
        for f in ("claims", "why_surfaced", "title", "item_id"):
            if f in view and not isinstance(view[f], str):
                errors.append(f"{key}: judge field {f!r} must be a "
                              "string")
        for f in ("evidence_refs", "related_records"):
            if f in view and not isinstance(view[f], list):
                errors.append(f"{key}: judge field {f!r} must be an "
                              "array")
        rr = view.get("related_records")
        for entry in rr if isinstance(rr, list) else []:
            if not isinstance(entry, dict):
                errors.append(f"{key}: related_records entries must be "
                              "objects with keys "
                              f"{RELATED_RECORD_SCHEMA_FIELDS}")
                continue
            extra = set(entry) - set(RELATED_RECORD_SCHEMA_FIELDS)
            if extra:
                errors.append(
                    f"{key}: related_records keys outside the positive "
                    f"schema {RELATED_RECORD_SCHEMA_FIELDS}: "
                    f"{sorted(extra)}")
            for f in ("kind", "id"):
                if f in entry and not isinstance(entry[f], str):
                    errors.append(f"{key}: related_records {f!r} must "
                                  "be a string")
    # 2. recursive machinery-equivalent scan over the WHOLE document
    for here in _recursive_machinery_keys(packet_doc):
        errors.append(f"packet leaked forbidden machinery key at "
                      f"{here}")
    if schema == "rre_v1_pairwise":
        items = pk.get("items") or {}
        if isinstance(items, dict) and \
                set(items.keys()) != {"ITEM_1", "ITEM_2"}:
            errors.append(f"expected exactly ITEM_1+ITEM_2, got "
                          f"{sorted(items)}")
        states = {}
        for key in ("ITEM_1", "ITEM_2"):
            it = items.get(key) if isinstance(items, dict) else None
            it = it or {}
            states[key] = validate_item_provenance(it,
                                                   evidence_inventory)
            if not (it or {}).get("item_id"):
                errors.append(f"{key}: missing item_id")
            if not (it.get("marker_synthetic") is True
                    or it.get("marker_synthetic") is False):
                if "marker_synthetic" not in pk and \
                        "marker_synthetic" not in it:
                    errors.append(f"{key}: synthetic/provenance marker "
                                  "absent")
    else:
        states = {}
        lists = pk.get("lists") or {}
        if isinstance(lists, dict) and \
                set(lists.keys()) != {"LIST_1", "LIST_2"}:
            errors.append(f"expected exactly LIST_1+LIST_2, got "
                          f"{sorted(lists)}")
        for key in ("LIST_1", "LIST_2"):
            for view in (lists.get(key) or []) \
                    if isinstance(lists, dict) else []:
                if not (view or {}).get("item_id"):
                    errors.append(f"{key}: missing item_id")
    return {"ok": not errors, "errors": errors,
            "provenance_states": states,
            "inventory_supplied": evidence_inventory is not None}


# ---------------------------------------------------------------------------
# Unblinding (mechanically bound sidecars; pairwise AND listwise)
# ---------------------------------------------------------------------------


def check_sidecar_binding(blinded_packet: dict, blind_sidecar: dict,
                          *, binding_secret) -> None:
    """R5 (amendment 2): the stored reverse_map is NEVER trusted, and
    the sidecar is never trusted without its blind-time secret. The
    expected slot assignment is RE-DERIVED from the frozen assignment
    function and frozen salt over the sidecar's declared arm set, the
    supplied slots/reverse_map must equal the expectation exactly, and
    the sidecar's HMAC must verify under the caller's binding secret
    (so relabeled-arm forgeries fail closed even though the unkeyed
    digest could be recomputed).

    Refuses: unknown binding versions; missing/short secrets; arm sets
    that are not exactly two distinct non-empty arms (fantasy arms,
    three-arm maps, duplicates); slot maps with collisions or duplicate
    mappings; flipped or same-layout wrong sidecars; and any packet
    whose bytes differ from the authenticated ones.
    """
    if not isinstance(blind_sidecar, dict):
        raise PacketBindError("sidecar must be a document")
    if blind_sidecar.get("binding_version") != SIDECAR_BINDING_VERSION:
        raise PacketBindError(
            f"sidecar binding_version "
            f"{blind_sidecar.get('binding_version')!r} unknown")
    if not blind_sidecar.get("binding_mac"):
        raise PacketBindError(
            "sidecar carries no binding MAC — refusing an "
            "unauthenticated sidecar")
    secret = _normalize_secret(binding_secret)
    schema = blinded_packet.get("packet_schema")
    expected_tokens = {"ITEM_1", "ITEM_2"} \
        if schema == "rre_v1_pairwise" else {"LIST_1", "LIST_2"}
    packet_id = blinded_packet.get("packet_id")
    _require_packet_id(packet_id)
    arms = blind_sidecar.get("arms")
    if not isinstance(arms, list) or len(arms) != 2 \
            or not all(isinstance(a, str) and a.strip() for a in arms) \
            or len(set(arms)) != 2:
        raise PacketBindError(
            f"sidecar arms must be exactly two distinct non-empty arm "
            f"id strings, got {arms!r}")
    rev = blind_sidecar.get("reverse_map")
    slots = blind_sidecar.get("slots")
    if not isinstance(rev, dict) or not isinstance(slots, dict) \
            or not all(isinstance(k, str) for k in rev) \
            or not all(isinstance(k, str) for k in slots) \
            or not all(isinstance(v, str) for v in rev.values()) \
            or not all(isinstance(v, str) for v in slots.values()):
        raise PacketBindError(
            "sidecar slots/reverse_map must be objects with string "
            "keys and values")
    if set(rev.values()) != expected_tokens:
        raise PacketBindError("sidecar reverse map incomplete")
    if set(rev.keys()) != set(arms):
        raise PacketBindError(
            f"sidecar reverse_map keys must be exactly the declared "
            f"arm set {sorted(arms)}, got {sorted(rev)}")
    if set(slots.values()) != set(arms) or \
            set(slots.keys()) != {"0", "1"}:
        raise PacketBindError(
            f"sidecar slots must map 0/1 onto exactly the two declared "
            f"arms, got {slots!r}")
    expected = canonical_binding(packet_id, arms, schema)
    if slots != expected["slots"] or rev != expected["reverse_map"]:
        raise PacketBindError(
            "sidecar slot map does not match the re-derived frozen "
            f"assignment for packet {packet_id!r} arms {sorted(arms)}: "
            f"supplied {slots!r}/{rev!r}, expected "
            f"{expected['slots']!r}/{expected['reverse_map']!r}")
    if blind_sidecar.get("packet_id") != packet_id:
        raise PacketBindError(
            f"sidecar packet_id {blind_sidecar.get('packet_id')!r} does "
            f"not bind packet {packet_id!r}")
    got = blind_sidecar.get("blinded_packet_sha256")
    want = _binding_digest(blinded_packet, expected["arms"],
                           expected["reverse_map"])
    if got != want:
        raise PacketBindError(
            f"sidecar blinded-packet binding digest mismatch (sidecar "
            f"{str(got)[:12]}… vs packet {want[:12]}…)")
    want_mac = _binding_mac(secret, blinded_packet, expected["arms"],
                            expected["reverse_map"])
    supplied_mac = blind_sidecar.get("binding_mac")
    if not isinstance(supplied_mac, str) or \
            not supplied_mac.isascii() or \
            not hmac.compare_digest(supplied_mac, want_mac):
        raise PacketBindError(
            "sidecar binding MAC verification failed — the sidecar was "
            "not sealed by the blind step holding this secret")


def unbind_result(blinded_packet: dict, blind_sidecar: dict,
                  judgments: dict, *, binding_secret,
                  dims_ratings=None,
                  transport_meta=None) -> dict:
    """Attach judge outcomes (slot-tokenized) plus sidecar identity.

    judgments: {question -> slot outcome}. Emits canonical deblinded
    fields `regret_preferred_arm` etc.; TIE/NEITHER remain as-is.
    Fails closed unless the sidecar is bound to this exact packet AND
    authenticated under the blind-time binding secret.
    """
    check_sidecar_binding(blinded_packet, blind_sidecar,
                          binding_secret=binding_secret)
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


def unbind_list_result(blinded_packet: dict, blind_sidecar: dict,
                       judgment: dict, *, binding_secret) -> dict:
    """R5: mechanical listwise unblinding — no manual LIST_n -> arm
    mapping. Validates the sidecar binding (re-derivation + HMAC under
    the blind-time secret), the outcome vocabulary, and the regret
    marks (each mark must reference an item_id in the marked list;
    repeats are deduplicated). Returns the post-unbind record shape
    consumed by aggregate_list_results."""
    check_sidecar_binding(blinded_packet, blind_sidecar,
                          binding_secret=binding_secret)
    rev = blind_sidecar["reverse_map"]
    slot_for_arm = {v: k for k, v in rev.items()}
    if set(slot_for_arm) != {"LIST_1", "LIST_2"}:
        raise PacketBindError("sidecar reverse map incomplete")
    outcome = (judgment or {}).get("outcome")
    if outcome not in LIST_OUTCOME_VALUES:
        raise PacketBindError(
            f"listwise outcome not in vocabulary: {outcome!r}")
    marks = (judgment or {}).get("regret_marks") or {}
    if set(marks.keys()) != {"LIST_1", "LIST_2"}:
        raise PacketBindError("listwise marks must cover LIST_1+LIST_2")
    marks_by_arm = {}
    for token, ids in marks.items():
        arm = slot_for_arm[token]
        listed = [v.get("item_id")
                  for v in blinded_packet["lists"].get(token, [])]
        valid = set(listed)
        deduped = []
        for i in ids:
            if i not in valid:
                raise PacketBindError(
                    f"regret mark {i!r} is not an item of {token}")
            if i not in deduped:
                deduped.append(i)
        marks_by_arm[arm] = deduped
    preferred = outcome
    if outcome in ("LIST_1", "LIST_2"):
        preferred = slot_for_arm[outcome]
    return {
        "packet_id": blinded_packet["packet_id"],
        "arms": {arm: [v.get("item_id")
                       for v in blinded_packet["lists"].get(tok, [])]
                 for arm, tok in rev.items()},
        "preferred_list": preferred,
        "regret_marks_by_arm": marks_by_arm,
    }


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
    baseline_arm, similarity diagnostics optional per arm
    (evaluator-side only; never judge-visible). Strict-win denominators
    = pairs with a strict preferred arm AND normal kind AND both sides
    provenance VALID. Provisional pairs are reported, never silently
    dropped, and never counted toward gate outcomes.

    R7 counting integrity: packet ids are deduplicated — an exact
    duplicate record is skipped and receipted in
    `duplicate_records_deduplicated`; the same id with different
    content raises PacketBindError. Only eligible decided packets
    (normal kind, both sides VALID, strict decision) feed
    `n_distinct_decided_packets`.
    """
    # R7 dedup pass — shared across questions so every question's
    # counts see the same deduplicated record set.
    seen: dict[str, str] = {}
    deduped = []
    dup_dedup_count = 0
    for rec in pair_records:
        pid = rec.get("packet_id")
        fp = canonical_doc_hash(rec)
        if pid is not None:
            if pid in seen:
                if seen[pid] != fp:
                    raise PacketBindError(
                        f"conflicting duplicate packet_id {pid!r}")
                dup_dedup_count += 1
                continue
            seen[pid] = fp
        deduped.append(rec)
    report = {}
    details = []
    for q in QUESTIONS:
        counts = {"A_strict_wins": 0, "B_strict_wins": 0,
                  "ties": 0, "neither": 0, "provisional_excluded": 0,
                  "duplicate_across_arms": 0,
                  "duplicate_records_deduplicated": dup_dedup_count,
                  "pairs_total": 0}
        wins_by_arm: dict[str, int] = {}
        decided_packet_ids: set = set()
        counter_similarity_wins_challenger = 0
        sim_axis_present = False
        for rec in deduped:
            counts["pairs_total"] += 1
            j = rec.get("judgments") or {}
            pref = (j.get(q) or {}).get("preferred_arm")
            chal = rec.get("challenger_arm")
            base = rec.get("baseline_arm")
            if pref is None:
                # amendment-2 hygiene: a missing judgment must never
                # silently become NEITHER (or any other outcome)
                raise PacketBindError(
                    f"missing judgment for {q} in packet "
                    f"{rec.get('packet_id')!r}")
            if pref not in (OUTCOME_TIE, OUTCOME_NEITHER, chal, base):
                raise PacketBindError(
                    f"unknown preferred_arm {pref!r} for packet "
                    f"{rec.get('packet_id')!r} (arms "
                    f"{chal!r}/{base!r})")
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
            decided_packet_ids.add(rec.get("packet_id"))
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
            "n_distinct_decided_packets": len(decided_packet_ids),
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
# Finite-set outcome + diagnostic coverage (ISEM lesson, dual output)
# ---------------------------------------------------------------------------


def finite_set_outcome(agg: dict, question: str) -> dict:
    """Exact verdict over the judged packet set for one question.

    This is a statement about THIS evaluated set and nothing else."""
    block = agg[question]
    c = block["counts"]
    cw, bw = c["B_strict_wins"], c["A_strict_wins"]
    denom = c["A_strict_wins"] + c["B_strict_wins"]
    # pairs_total already excludes deduplicated exact duplicates (the
    # R7 dedup pass runs before this accounting), so only the two
    # record-level exclusion classes subtract here.
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


def diagnostic_floor_status(agg: dict, question: str) -> dict:
    """R1: NON-INFERENTIAL bookkeeping label over the judged set.

    MINIMUM_DIAGNOSTIC_FLOOR_MET says only that the judged set reached
    the frozen minimum-size floor (>=5 decided strict conformance pairs
    across >=5 distinct eligible decided packets). It makes NO claim
    about populations, generalization, or statistical significance;
    such claims require a separate preregistered statistical design
    that does not exist here.
    """
    block = agg[question]
    n_decided = block["n_decided_strict_conformance"]
    n_packets = block["n_distinct_decided_packets"]
    met = (n_decided >= MIN_DECIDED_PAIRS_FOR_DIAGNOSTIC_FLOOR
           and n_packets
           >= MIN_DISTINCT_DECIDED_PACKETS_FOR_DIAGNOSTIC_FLOOR)
    return {
        "status": DIAGNOSTIC_FLOOR_MET if met else DIAGNOSTIC_FLOOR_NOT_MET,
        "n_decided_strict_conformance": n_decided,
        "threshold_decided_pairs":
            MIN_DECIDED_PAIRS_FOR_DIAGNOSTIC_FLOOR,
        "n_distinct_decided_packets": n_packets,
        "threshold_distinct_packets":
            MIN_DISTINCT_DECIDED_PACKETS_FOR_DIAGNOSTIC_FLOOR,
        "interpretation":
            "non-inferential diagnostic-coverage bookkeeping; NOT a "
            "generalization or statistical-sufficiency claim",
    }


def primary_falsifier_verdict(finite: dict, floor: dict,
                              agg_question: dict) -> dict:
    """Frozen falsifier gate (regret question only), amendment-1
    semantics.

    PASSED_PRIMARY_FALSIFIER requires ALL of:
      - finite CHALLENGER_PREFERRED over the judged set;
      - similarity axis TESTED (R2: an untested axis can never pass);
      - >=1 counter-similarity challenger win (semantic similarity
        alone does not pass);
      - MINIMUM_DIAGNOSTIC_FLOOR_MET.
    The verdict is a statement about the frozen finite evaluation set
    ONLY — finite-set evidence against the baseline. It is NOT a
    population-superiority, generalization, or production-promotion
    claim.

    Verdict ladder:
      PASSED_PRIMARY_FALSIFIER                      all conjuncts hold
      CHALLENGER_WINS_FINITE_SET                    finite win, floor
                                                    pending (axis tested,
                                                    counter-sim present)
      CHALLENGER_WINS_FINITE_SET_SIMILARITY_UNTESTED
                                                    finite win, axis
                                                    UNTESTED — cannot pass
      SIMILARITY_CONFOUNDED_NOT_PASSING             every challenger win
                                                    sat on higher
                                                    similarity
      BASELINE_PREFERRED / DEAD_HEAT / DECISION_INERTIA / NOT_EVALUABLE
                                                    mirrored finite states
    """
    status = finite["status"]
    verdict = {
        "CHALLENGER_PREFERRED": None,  # refined below
        "BASELINE_PREFERRED": "BASELINE_PREFERRED",
        "DEAD_HEAT": "DEAD_HEAT",
        "DECISION_INERTIA": "DECISION_INERTIA",
        "NOT_EVALUABLE": "NOT_EVALUABLE",
    }[status]
    if status == "CHALLENGER_PREFERRED":
        if not agg_question["similarity_axis_tested"]:
            verdict = "CHALLENGER_WINS_FINITE_SET_SIMILARITY_UNTESTED"
        elif agg_question["counter_similarity_wins_challenger"] < 1:
            verdict = "SIMILARITY_CONFOUNDED_NOT_PASSING"
        elif floor["status"] == DIAGNOSTIC_FLOOR_MET:
            verdict = "PASSED_PRIMARY_FALSIFIER"
        else:
            verdict = "CHALLENGER_WINS_FINITE_SET"
    return {
        "verdict": verdict,
        "finite_set_status": finite["status"],
        "diagnostic_coverage_status": floor["status"],
        "similarity_axis_tested": agg_question["similarity_axis_tested"],
        "counter_similarity_wins_challenger":
            agg_question["counter_similarity_wins_challenger"],
        "untested_axes": ([] if agg_question["similarity_axis_tested"]
                          else ["semantic_similarity_diagnostic"]),
        "scope": "finite_set_only — no population, generalization, or "
                 "production-promotion claim",
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
             feedback_events=None,
             judgments_evidence_class: str) -> dict:
    """Assemble the complete RRE v1 report (dual output, falsifier).

    R6: `judgments_evidence_class` is REQUIRED (no default) and must
    come from the frozen vocabulary — the headline verdict is never
    emitted detached from its epistemic provenance, not even by an
    accidental default. Offline synthetic demonstrations pass
    EVIDENCE_CLASS_SYNTHETIC; curated blinded-judge pipelines pass
    EVIDENCE_CLASS_CURATED. Anything else fails closed.
    """
    if judgments_evidence_class not in JUDGMENT_EVIDENCE_CLASSES:
        raise PacketBindError(
            f"judgments_evidence_class "
            f"{judgments_evidence_class!r} outside the frozen "
            f"vocabulary {JUDGMENT_EVIDENCE_CLASSES}")
    agg = aggregate_pair_results(pair_records)
    per_question = {}
    for q in QUESTIONS:
        fin = finite_set_outcome(agg, q)
        floor = diagnostic_floor_status(agg, q)
        entry = {
            "evidence_class": judgments_evidence_class,
            "finite_set_outcome": fin,
            "diagnostic_coverage_status": floor,
            "provisional_pairs_excluded":
                agg[q]["counts"]["provisional_excluded"],
            "duplicate_across_arms":
                agg[q]["counts"]["duplicate_across_arms"],
            "duplicate_records_deduplicated":
                agg[q]["counts"]["duplicate_records_deduplicated"],
        }
        if q == PRIMARY_QUESTION:
            falsifier = primary_falsifier_verdict(fin, floor, agg[q])
            falsifier["evidence_class"] = judgments_evidence_class
            entry["primary_falsifier"] = falsifier
        per_question[q] = entry
    overall = per_question[PRIMARY_QUESTION]["primary_falsifier"]
    feedback_block = None
    if feedback_events:
        feedback_block = feedback_role_summary(feedback_events)
    return {
        "evaluator": EVALUATOR_ID,
        "amendment": AMENDMENT_ID,
        "generated": time.strftime("%Y-%m-%dT%H%M%S"),
        "arms": {"challenger": challenger_arm,
                 "baseline": baseline_arm},
        "evidence_class": judgments_evidence_class,
        "scope": "finite evaluation set only — no population-level, "
                 "generalization, or production-promotion claim",
        "judgment_dimensions":
            "dimension ratings, when present on records (dims_ratings), "
            "are DIAGNOSTIC-ONLY: reported per packet, never aggregated "
            "into any verdict (no aggregation function exists or is "
            "planned without amendment)",
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
                          extra=None, recent_surfaced_items=None,
                          *, binding_secret) -> dict:
    """Helper used by tests/demo: validate, unbind, produce a pair
    record consumable by evaluate(). The PASSED sidecar performs the
    unblinding under the PASSED blind-time binding secret — a
    mismatched sidecar or wrong secret fails closed here too. The
    rebuild mirrors the caller's build (same recent_surfaced_items,
    same secret) or the binding check fails closed."""
    pkg = build_blinded_packet(packet_id,
                               {baseline_arm: [item_a],
                                challenger_arm: [item_b]},
                               recent_surfaced_items=recent_surfaced_items,
                               binding_secret=binding_secret)
    val = validate_packet({"blinded_packet": pkg["blinded_packet"]},
                          evidence_inventory)
    if not val["ok"]:
        raise PacketBindError(f"demo packet invalid: {val['errors']}")
    rec = unbind_result(pkg["blinded_packet"], sidecar, judgments,
                        binding_secret=binding_secret)
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
    "docs/handoffs/interest-intelligence/"
    "recommendation-regret-evaluator-v1/"
    "ARCHITECT_AMENDMENT_1_REVIEW_REPAIR.md",
    "docs/handoffs/interest-intelligence/"
    "recommendation-regret-evaluator-v1/"
    "ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING.md",
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
