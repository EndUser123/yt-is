"""Immutable verdict receipts + the separate promotion command (E-gate P0).

Batteries/evaluators may ONLY emit receipts via write_receipt(): a file
with a content hash, suite identity, gate results, verdict, and a
promotion_authorized flag. Nothing in this module lets an evaluator touch
active_generation.

ef.promote is the single promotion authority: it mechanically verifies a
promotion-authorized PASS receipt (identity, integrity hash, candidate
generation, BuildSpec/build_id, required gate completeness, freshness,
expected current active generation, no retracted/stale marker) and only
then performs the atomic switch. Fail-closed, idempotent.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

EF_DATA = Path("P:/.data/yt-is/ef")
RECEIPTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "evidence-fabric"
RETRACTED_DIR = EF_DATA

# Suites allowed to authorize promotion (single source; batteries cite it)
PROMOTION_AUTHORIZED_SUITES = {"c4_final_battery", "c9_final_battery"}


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True)
                          .encode()).hexdigest()


def write_receipt(suite: str, gates: dict, verdict_pass: bool,
                  promotion_authorized: bool = False,
                  required_gates: list[str] | None = None,
                  out_dir: Path | None = None) -> Path:
    """Emit an immutable verdict receipt. Never touches promotion state."""
    payload = {
        "suite": suite,
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "promotion_authorized": promotion_authorized and verdict_pass,
        "required_gates": required_gates or sorted(gates),
        "gates": gates,
        "verdict": "PASS" if verdict_pass else "FAIL",
    }
    # content hash excludes emitted_at: identical verdicts are identical
    # receipts -> same file -> immutability via filename collision
    content = {k: v for k, v in payload.items() if k != "emitted_at"}
    digest = _hash(content)
    doc = {"payload": payload, "receipt_sha256": digest}
    out = (out_dir or RECEIPTS_DIR) / f"receipt_{suite}_{digest[:12]}.json"
    if out.exists():
        raise FileExistsError(f"receipt {out.name} already exists "
                              f"(immutable)")
    out.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return out


def load_and_verify(receipt_path: Path) -> dict:
    doc = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload = doc["payload"]
    content = {k: v for k, v in payload.items() if k != "emitted_at"}
    if _hash(content) != doc["receipt_sha256"]:
        raise ValueError(f"receipt integrity failure: {receipt_path}")
    return doc


def promote_from_receipt(receipt_path: Path,
                         expected_active: int = 0) -> dict:
    """The ONLY path to promotion. Fails closed on every mismatch."""
    from . import buildspec, freshness

    doc = load_and_verify(receipt_path)          # integrity
    p = doc["payload"]

    def fail(msg: str) -> None:
        raise ValueError(f"PROMOTION REFUSED: {msg}")

    if p["suite"] not in PROMOTION_AUTHORIZED_SUITES:
        fail(f"suite {p['suite']!r} is not promotion-authorized")
    if not p.get("promotion_authorized"):
        fail("receipt not promotion-authorized")
    if p["verdict"] != "PASS":
        fail(f"verdict is {p['verdict']}")
    # complete required gate set present and passing
    gates = p.get("gates", {})
    for name in p.get("required_gates", []):
        if name not in gates:
            fail(f"required gate {name!r} missing from receipt")
        if not gates[name].get("pass"):
            fail(f"required gate {name!r} not passing")
    # retracted receipts refused
    for marker in RETRACTED_DIR.glob("promotion.retracted.*.json"):
        m = json.loads(marker.read_text(encoding="utf-8"))
        if m.get("evidence", {}).get("receipt_sha256") == doc["receipt_sha256"] \
                or m.get("receipt_sha256") == doc["receipt_sha256"]:
            fail(f"receipt {doc['receipt_sha256'][:12]} was retracted")
    # candidate generation + buildspec
    spec = buildspec.load_spec()
    gen = spec["generation"]
    if p.get("gates", {}).get("structural", {}).get("candidate_generation") \
            not in (None, gen):
        fail("receipt candidate generation != buildspec generation")
    build_id = f"generation/gen{gen}-{buildspec.spec_digest(spec)}"
    if p.get("gates", {}).get("namespace", {}).get("build_id") not in \
            (None, build_id):
        fail("receipt build_id != current BuildSpec build_id")
    # expected current active generation
    if buildspec.active_generation() != expected_active:
        fail(f"active_generation is {buildspec.active_generation()}, "
             f"expected {expected_active}")
    # freshness at promotion time
    st = freshness.load_state()
    lag = freshness.compute_lag(st.get("indexed_watermark", ""))
    if lag["index_lag_count"] > 50:
        fail(f"index lag {lag['index_lag_count']} > 50")

    evidence = {"receipt": str(receipt_path),
                "receipt_sha256": doc["receipt_sha256"],
                "suite": p["suite"],
                "lag_at_promotion": lag["index_lag_count"]}
    return buildspec.promote(gen, evidence=evidence)
