"""Single authoritative definition of the v2 interest-inference contract.

One module defines the enum vocabularies, the field surface, and both
provider-facing JSON Schemas (inference batches and the reconciliation
wrapper). ``scripts/build_interest_graph.py`` imports the enums from here
and keeps ``validate_inference`` as the independent semantic layer, so the
schema and the validator cannot drift apart silently.

Scope boundary (unchanged): a JSON Schema can mechanically constrain
shape — top-level arrays, required fields, enums, numeric bounds,
nullability, arrays vs scalars. It CANNOT enforce same-payload dynamic
references (parent / related_to / questions.interest /
regret.related_interests against actually returned names) or cluster-id
membership; those stay in post-schema semantic validation.

The conformance checker below is deliberately tiny: it understands exactly
the constructs this module emits, and exists so tests and the contract
bakeoff can independently verify provider enforcement without adding a
JSON-Schema dependency.

Pure logic: no database, network, or subprocess access.
"""

from __future__ import annotations

import json

KINDS = ("domain", "topic", "subtopic", "method", "monitor")
TEMPORAL_STATES = ("durable", "active", "current_problem", "episodic",
                   "emerging", "dormant")
STANCES = ("curiosity", "learning", "project", "monitoring", "entertainment")
OBSERVED_VS_INFERRED = ("observed", "inferred", "inferred_adjacent")
QUESTION_STATUSES = ("open", "watching")
REGRET_LABELS = ("inferred_adjacent",)
RECONCILIATION_DECISIONS = ("kept", "merged", "discarded")

CONFIDENCE_MIN = 0.0
CONFIDENCE_MAX = 1.0


def _nullable_str() -> dict:
    return {"anyOf": [{"type": "string"}, {"type": "null"}]}


def _confidence() -> dict:
    return {"type": "number",
            "minimum": CONFIDENCE_MIN,
            "maximum": CONFIDENCE_MAX}


def _cluster_ids(*, unique: bool) -> dict:
    # 2026-08-26 AMENDMENT 2: `uniqueItems` is HTTP-400-rejected by the
    # codex/gpt-5.6-luna structured-output endpoint (bisected live;
    # minItems/$ref/bounds accepted), so duplicates are NOT schema-blocked
    # for any caller. The mechanical validator keeps enforcing
    # no-duplicate interest cluster_ids (dupes=True semantics); regret
    # candidates allow duplicates exactly as before. The `unique`
    # argument is retained so call sites keep expressing intent.
    del unique
    return {"type": "array", "items": {"type": "integer"},
            "minItems": 1}


def _interest_schema() -> dict:
    # Field-for-field mirror of validate_inference's interest checks.
    # interests' cluster_ids forbid duplicates; uniqueness of names,
    # reference resolution, cycles, and non-empty string content are
    # semantic-layer concerns and intentionally absent here.
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": list(KINDS)},
            "parent": _nullable_str(),
            "temporal_state": {"type": "string",
                               "enum": list(TEMPORAL_STATES)},
            "stance": {"type": "string", "enum": list(STANCES)},
            "confidence": _confidence(),
            "observed_vs_inferred": {
                "type": "string", "enum": list(OBSERVED_VS_INFERRED)},
            "goal": _nullable_str(),
            "information_need": _nullable_str(),
            "cluster_ids": _cluster_ids(unique=True),
            "evidence_summary": {"type": "string"},
            "counterevidence": _nullable_str(),
            "related_to": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "name", "kind", "parent", "temporal_state", "stance",
            "confidence", "observed_vs_inferred", "goal",
            "information_need", "cluster_ids", "evidence_summary",
            "counterevidence", "related_to",
        ],
        "additionalProperties": False,
    }


def _v2_result_schema() -> dict:
    """Result-object body WITHOUT its own '$defs' key.

    Callers own the '$defs' layer (AMENDMENT 3: the reconciliation
    wrapper previously embedded inference_output_schema()'s whole dict,
    producing nested '$defs' that the provider endpoint rejects with
    HTTP 400). Every $ref below resolves against the caller's TOP-level
    '$defs'.
    """
    return {
        "type": "object",
        "properties": {
            "inferred_interests": {
                "type": "array", "items": {"$ref": "#/$defs/interest"},
                "minItems": 1},
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "interest": {"type": "string"},
                        "status": {"type": "string",
                                   "enum": list(QUESTION_STATUSES)},
                    },
                    "required": ["text", "interest", "status"],
                    "additionalProperties": False,
                }},
            "regret_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "why": {"type": "string"},
                        "label": {"type": "string",
                                  "enum": list(REGRET_LABELS)},
                        "confidence": _confidence(),
                        "cluster_ids": _cluster_ids(unique=False),
                        "related_interests": {
                            "type": "array",
                            "items": {"type": "string"}},
                    },
                    "required": ["topic", "why", "label", "confidence",
                                 "cluster_ids", "related_interests"],
                    "additionalProperties": False,
                }},
        },
        "required": ["inferred_interests", "questions",
                     "regret_candidates"],
        "additionalProperties": False,
    }


def inference_output_schema() -> dict:
    """Provider-native strict output schema for one inference payload."""
    return {
        "type": "object",
        "$defs": {"interest": _interest_schema()},
        "properties": _v2_result_schema()["properties"],
        "required": _v2_result_schema()["required"],
        "additionalProperties": False,
    }


def reconciliation_output_schema() -> dict:
    """Strict output schema for one reconciliation group call.

    Single-level '$defs': interest / v2_result / disposition all sit at
    the root and every $ref resolves there. Constraint content equals the
    embedded-payload form used before AMENDMENT 3 (which the endpoint
    rejected); only reference layout changed.
    """
    return {
        "type": "object",
        "$defs": {
            "interest": _interest_schema(),
            "v2_result": _v2_result_schema(),
            "disposition": {
                "type": "object",
                "properties": {
                    "fragment_id": {"type": "string"},
                    "decision": {"type": "string",
                                 "enum": list(RECONCILIATION_DECISIONS)},
                    "target_interest": _nullable_str(),
                    "reason": {"type": "string"},
                },
                "required": ["fragment_id", "decision", "target_interest",
                             "reason"],
                "additionalProperties": False,
            },
        },
        "properties": {
            "final": {"$ref": "#/$defs/v2_result"},
            "fragment_dispositions": {
                "type": "array", "items": {"$ref": "#/$defs/disposition"}},
        },
        "required": ["final", "fragment_dispositions"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Minimal conformance checker (no external jsonschema dependency)
# ---------------------------------------------------------------------------

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: (isinstance(v, (int, float))
                         and not isinstance(v, bool)),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)),
    "null": lambda v: v is None,
}


def conformance_errors(instance, schema, path="$",
                       defs: dict | None = None) -> list[str]:
    """Structural violations of `schema` at `instance`, human-readable.

    Understands exactly: type (incl. anyOf unions), enum, required,
    properties + additionalProperties:false, items, minItems, uniqueItems,
    minimum/maximum, $ref into #/$defs. Anything else is not part of this
    contract's vocabulary by construction.
    """
    errs: list[str] = []

    def fail(msg: str) -> None:
        errs.append(f"{path}: {msg}")

    if "$defs" in schema:
        defs = {**(defs or {}), **schema["$defs"]}

    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        resolved = (defs or {}).get(name)
        if resolved is None:
            return [f"{path}: unresolvable $ref {schema['$ref']!r}"]
        return conformance_errors(instance, resolved, path, defs)

    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            if not conformance_errors(instance, branch, path, defs):
                return []
        kinds = "/".join(b.get("type", "?") for b in schema["anyOf"])
        fail(f"does not match any allowed type ({kinds})")
        return errs

    expected = schema.get("type")
    if expected is not None and not _TYPE_CHECKS[expected](instance):
        fail(f"expected {expected}, got {_json_type_name(instance)}")
        return errs

    if "enum" in schema and instance not in schema["enum"]:
        fail(f"{instance!r} not in enum {schema['enum']}")
    if isinstance(instance, (int, float)) and \
            not isinstance(instance, bool) and expected == "number":
        lo = schema.get("minimum")
        hi = schema.get("maximum")
        if lo is not None and instance < lo:
            fail(f"{instance} < minimum {lo}")
        if hi is not None and instance > hi:
            fail(f"{instance} > maximum {hi}")
    if expected == "array":
        min_items = schema.get("minItems", 0)
        if len(instance) < min_items:
            fail(f"fewer than {min_items} items")
        if schema.get("uniqueItems") and len(instance) != len(
                {json.dumps(i, sort_keys=True) for i in instance}):
            fail("items are not unique")
        items = schema.get("items")
        if items:
            for i, element in enumerate(instance):
                errs.extend(conformance_errors(element, items,
                                               f"{path}[{i}]", defs))
    if expected == "object":
        for key in schema.get("required", []):
            if key not in instance:
                fail(f"missing required property {key!r}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    fail(f"unexpected property {key!r}")
        for key, subschema in props.items():
            if key in instance:
                errs.extend(conformance_errors(
                    instance[key], subschema, f"{path}.{key}", defs))
    return errs


def _json_type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
