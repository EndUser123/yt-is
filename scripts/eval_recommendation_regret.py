#!/usr/bin/env python
"""RRE v1 CLI — recommendation regret evaluator (amendment 1).

Subcommands (all offline unless --transport codex is explicitly given):
  validate        fail-closed validation of a blinded packet document
                  (optional --evidence-inventory to prove provenance)
  blind           build blinded packet + BOUND sidecar from an arms
                  JSON file
  judge           run the frozen judge over a blinded packet (codex
                  transport; refuses to run by default; ALWAYS
                  validates the packet first and fails closed on
                  unparsed judge output)
  evaluate        aggregate unblinded pair records into the RRE v1
                  report (dual output + primary falsifier; requires
                  --evidence-class)
  demo            full offline pass over synthetic fixtures only
  verify-freeze   re-verify FREEZE_RECEIPT.json artifact hashes
  freeze-receipt  write/update FREEZE_RECEIPT.json from current files

Contamination posture: this CLI operates on packet/result documents.
It has no path to any ranking implementation and performs no live
ranking run; the private regret/usefulness holdout is curated later by
an independent session, never here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ef import eval_recommendation_regret as rre  # noqa: E402

HANDOFF_DIR = (REPO_ROOT / "docs" / "handoffs" / "interest-intelligence"
               / "recommendation-regret-evaluator-v1")
FREEZE_RECEIPT = HANDOFF_DIR / "FREEZE_RECEIPT.json"

# Historical record (REVIEW-freeze-20260827.md; verified by the fresh
# freeze reviewer before rejection). The rejected v1 bytes are preserved
# under rejected-candidate-v1/ next to the amendment.
ORIGINAL_V1_FROZEN_HASHES = {
    "ef/eval_recommendation_regret.py":
        "95369544be76f418e9ae86ecbe0882af27ee051e5f9722b85f17444f85b05e85",
    "scripts/eval_recommendation_regret.py":
        "26f265b6f6ea559138717c1b0813407e7f6872584a233476e819fe0fa7712ce5",
    "tests/test_eval_recommendation_regret.py":
        "0c56aff6cfcae3cd700d17121937f012a4e8a8f803408af680178a3df79ab992",
    "docs/handoffs/interest-intelligence/"
    "recommendation-regret-evaluator-v1/METRIC_PLAN_PREREGISTRATION.md":
        "3308d67d7deb889fb64d543b808d714cf82d0ef9030cc8fb1d29dc1aa247b255",
}
REVIEW_REPORT_REL = ("docs/handoffs/interest-intelligence/"
                     "recommendation-regret-evaluator-v1/"
                     "REVIEW-freeze-20260827.md")
REVIEW_COMMIT = "732e2cfa5dbe2848421b48cf5b46d9bf4c68039"


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_inventory(args):
    if getattr(args, "evidence_inventory", None):
        return _load_json(Path(args.evidence_inventory))
    return None


def cmd_validate(args) -> int:
    doc = _load_json(args.packet)
    val = rre.validate_packet(doc, _load_inventory(args))
    print(json.dumps(val, indent=2))
    return 0 if val["ok"] else 2


def cmd_blind(args) -> int:
    spec = _load_json(args.arms_file)
    packet_id = spec.get("packet_id")
    arms = {a["arm"]: a["items"] for a in spec["arms"]}
    try:
        if "listwise" in spec.get("mode", "pairwise"):
            pkg = rre.build_blinded_list_packet(
                packet_id, arms, k=int(spec.get("k", 5)),
                recent_surfaced_items=spec.get("recent_surfaced_items"))
        else:
            pkg = rre.build_blinded_packet(
                packet_id, arms,
                recent_surfaced_items=spec.get(
                    "recent_surfaced_items"))
    except rre.PacketBindError as exc:
        print(f"blind refused: {exc}", file=sys.stderr)
        return 2
    out = {"blinded_packet": pkg["blinded_packet"]}
    if args.sidecar_out:
        Path(args.sidecar_out).write_text(
            json.dumps(pkg["blind_sidecar"], indent=1),
            encoding="utf-8")
        out["sidecar_written_to"] = args.sidecar_out
    else:
        # Sidecar MUST be stored sealed until judging completes; refuse
        # to co-mingle it with what a judge could see.
        print("refusing to inline sidecar without --sidecar-out",
              file=sys.stderr)
        return 2
    if args.secret_out:
        # The binding secret is the out-of-band half of the sidecar's
        # HMAC: store it beside NEITHER the packet NOR the sidecar.
        Path(args.secret_out).write_text(pkg["binding_secret"],
                                         encoding="utf-8")
        out["binding_secret_written_to"] = args.secret_out
    else:
        print("refusing to inline binding secret without --secret-out",
              file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


def cmd_judge(args) -> int:
    if not args.transport:
        print("judge transport refused without explicit --transport "
              "(no real judging happens at freeze time)", file=sys.stderr)
        return 3
    doc = _load_json(args.packet)
    # R3: no validation bypass path — the packet is validated (leak
    # scan + allowlist) BEFORE anything is rendered or invoked.
    val = rre.validate_packet(doc, _load_inventory(args))
    if not val["ok"]:
        print("packet validation failed; refusing to judge:",
              file=sys.stderr)
        for e in val["errors"]:
            print(f"  - {e}", file=sys.stderr)
        return 5
    pk = doc.get("blinded_packet") or doc
    recent = pk.get("recent_surfaced_items") or []
    listwise = pk.get("packet_schema") == "rre_v1_listwise"
    if listwise:
        prompt = rre.render_listwise_prompt(
            recent,
            json.dumps(pk["lists"]["LIST_1"], ensure_ascii=False),
            json.dumps(pk["lists"]["LIST_2"], ensure_ascii=False))
    else:
        prompt = rre.render_pairwise_prompt(
            args.question_text or "Which item would the operator most "
                                  "regret missing?",
            recent,
            json.dumps(pk["items"]["ITEM_1"], ensure_ascii=False),
            json.dumps(pk["items"]["ITEM_2"], ensure_ascii=False))
    transport = _codex_transport()
    raw = transport(prompt)
    if raw is None:
        print("judge transport failed", file=sys.stderr)
        return 4
    # Fail closed on unparsed judge output: garbage never exits 0.
    parsed = (rre.parse_listwise_result(raw) if listwise
              else rre.parse_outcome(raw))
    if parsed is None:
        print("judge output unparsed; refusing to emit a result",
              file=sys.stderr)
        print(raw, file=sys.stderr)
        return 6
    print(raw)
    print(json.dumps({"parsed": parsed}, indent=2))
    return 0


def _codex_transport():
    import shutil
    import subprocess
    import tempfile

    def transport(prompt_text: str):
        codex = shutil.which("codex")
        if not codex:
            return None
        for _ in range(rre.JUDGE_MAX_ATTEMPTS):
            pf = Path(tempfile.gettempdir()) / (
                f"rre-judge-{rre.sha256_bytes(prompt_text.encode())[:16]}"
                ".txt")
            pf.write_text(prompt_text, encoding="utf-8")
            try:
                res = subprocess.run(
                    [codex, "exec", "--json", "--ephemeral",
                     "-s", "read-only", "-m", rre.JUDGE_MODEL,
                     "-c",
                     "model_reasoning_effort="
                     f"{rre.JUDGE_REASONING_EFFORT}",
                     "-C", "P:/",
                     f"Read {pf} and return ONLY the JSON. No prose, "
                     "no markdown fences."],
                    capture_output=True, text=True,
                    timeout=rre.JUDGE_TIMEOUT_S, cwd="P:/",
                    creationflags=getattr(subprocess,
                                          "CREATE_NO_WINDOW", 0))
            except (subprocess.TimeoutExpired, OSError):
                continue
            if res.returncode != 0:
                continue
            return res.stdout
        return None

    return transport


def cmd_evaluate(args) -> int:
    records = []
    for p in args.results:
        loaded = json.loads(Path(p).read_text(encoding="utf-8"))
        records.extend(loaded if isinstance(loaded, list) else [loaded])
    try:
        report = rre.evaluate(
            records,
            challenger_arm=args.challenger_arm,
            baseline_arm=args.baseline_arm,
            judgments_evidence_class=args.evidence_class)
    except rre.PacketBindError as exc:
        print(f"evaluation refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


def cmd_demo(args) -> int:
    """Full offline demonstration on synthetic fixtures ONLY.

    Shows the dual output honestly at n=1: finite-set outcome computed
    exactly; diagnostic floor NOT MET by construction; falsifier
    untested-axis state. Evidence class synthetic_fixture.
    """
    items = rre.fixture_items()
    inventory = rre.FIXTURE_INVENTORY
    pkg = rre.build_blinded_packet(
        "fixture_demo_packet",
        {"A": [items[0]], "B": [items[1]]},
        recent_surfaced_items=["fixture_video_delta"])
    val = rre.validate_packet(pkg["blinded_packet"], inventory)
    # Simulated slot-keyed judge replies (offline; no transport).
    rec = rre.make_demo_pair_record(
        "fixture_demo_packet", items[0], items[1],
        pkg["blind_sidecar"],
        judgments={"WOULD_REGRET_MISSING": "ITEM_1",
                   "MORE_USEFUL_NOW": "TIE"},
        evidence_inventory=inventory,
        recent_surfaced_items=["fixture_video_delta"],
        binding_secret=pkg["binding_secret"])
    events = [
        {"verdict": "useful", "impression_id": "imp_fixture_1"},
        {"verdict": "known_already", "impression_id": "imp_fixture_2"},
        {"verdict": "wrong_inference", "impression_id": None},
        {"verdict": "investigate",
         "annotations": [{"exclude_from_evaluation": True}]},
    ]
    report = rre.evaluate(
        [rec], feedback_events=events,
        judgments_evidence_class=rre.EVIDENCE_CLASS_SYNTHETIC)
    demo_pack = {
        "demo_marker_synthetic": True,
        "evidence_class": rre.EVIDENCE_CLASS_SYNTHETIC,
        "provenance_validation": val,
        "listwise_overlap_demo": rre.overlap_at_k(
            ["v1", "v2", "v3"], ["v2", "v9", "v3"]),
        "report": report,
    }
    print(json.dumps(demo_pack, indent=2))
    return 0


def cmd_verify_freeze(_args) -> int:
    drift = rre.verify_manifest(REPO_ROOT, FREEZE_RECEIPT)
    if drift:
        for d in drift:
            print("DRIFT:", d, file=sys.stderr)
        return 3
    print("FROZEN MANIFEST OK — all artifact hashes reproduce")
    return 0


def cmd_freeze_receipt(args) -> int:
    artifacts = []
    for rel in rre.FROZEN_ARTIFACT_PATHS:
        p = REPO_ROOT / rel
        artifacts.append({"path": rel.replace("\\", "/"),
                          "sha256": rre.sha256_file(p)})
    receipt = {
        "receipt": "rre_v1_freeze_amendment_2",
        "agent": "zcode",
        "host": "zcode",
        "created_utc": args.timestamp,
        "amendment": rre.AMENDMENT_ID,
        "status":
            "RECOMMENDATION_EVALUATOR_AMENDMENT_2_READY_FOR_FRESH_REVIEW",
        "candidate_ranking_implementations": "NOT_YET_FROZEN (arm A/B/C)",
        "private_holdout": "DOES_NOT_EXIST_YET — later independent "
                           "curator creates it AFTER evaluator + "
                           "candidates freeze",
        "preregistration_doc":
            "docs/handoffs/interest-intelligence/"
            "recommendation-regret-evaluator-v1/"
            "METRIC_PLAN_PREREGISTRATION.md",
        "amendment_doc":
            "docs/handoffs/interest-intelligence/"
            "recommendation-regret-evaluator-v1/"
            "ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING.md",
        "amendment_docs": [
            "docs/handoffs/interest-intelligence/"
            "recommendation-regret-evaluator-v1/"
            "ARCHITECT_AMENDMENT_1_REVIEW_REPAIR.md",
            "docs/handoffs/interest-intelligence/"
            "recommendation-regret-evaluator-v1/"
            "ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING.md",
        ],
        "supersedes": {
            "rejected_candidate": "v1 (frozen 2026-08-27T07:33:05)",
            "original_frozen_hashes": ORIGINAL_V1_FROZEN_HASHES,
            "review_report": REVIEW_REPORT_REL,
            "review_commit": REVIEW_COMMIT,
            "review_verdict": "REJECTED",
            "v1_preserved_at":
                "docs/handoffs/interest-intelligence/"
                "recommendation-regret-evaluator-v1/"
                "rejected-candidate-v1/",
        },
        "judge_prompts_sha256": rre.FROZEN_PROMPTS_SHA256,
        "judge_model_config": {
            "model": rre.JUDGE_MODEL,
            "reasoning_effort": rre.JUDGE_REASONING_EFFORT,
            "timeout_s": rre.JUDGE_TIMEOUT_S,
            "max_attempts": rre.JUDGE_MAX_ATTEMPTS},
        "min_decided_pairs_for_diagnostic_floor":
            rre.MIN_DECIDED_PAIRS_FOR_DIAGNOSTIC_FLOOR,
        "min_distinct_decided_packets_for_diagnostic_floor":
            rre.MIN_DISTINCT_DECIDED_PACKETS_FOR_DIAGNOSTIC_FLOOR,
        "frozen_artifacts": artifacts,
        "contamination_record": [
            "never opened any file under .data/yt-is/private/",
            "no future ranking outputs inspected (none exist)",
            "Interest GT v1.1 not used as recommendation labels",
            "no real judge packets run (transport only via injected "
            "fakes in offline tests)",
            "feedback schema read from public contract source "
            "ef/personal_graph.py only; no live DB read"],
        "gate_activation_preconditions": [
            "fresh top-level freeze review of the amended candidate "
            "returns APPROVED",
            "accepted Interest state exists (inference recall/"
            "provenance gate passed and integrated)",
            "ranking candidates A (similarity+recency baseline) and B "
            "(goal/claim-aware) implemented and frozen elsewhere",
            "independent curator then builds ONE private blinded "
            "regret/usefulness packet set under this frozen evaluator",
            "single judging pass per packet set; no tuning afterward"],
        "final_reply_identity": {
            "session_name": "II Recommendation / Regret Ranking "
                            "Evaluator"},
    }
    if args.out:
        Path(args.out).write_text(json.dumps(receipt, indent=2),
                                  encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(json.dumps(receipt, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate")
    p.add_argument("--packet", required=True)
    p.add_argument("--evidence-inventory", default=None)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("blind")
    p.add_argument("--arms-file", required=True)
    p.add_argument("--sidecar-out", default=None)
    p.add_argument("--secret-out", default=None,
                   help="out-of-band destination for the binding "
                        "secret (HMAC key); required")
    p.set_defaults(func=cmd_blind)

    p = sub.add_parser("judge")
    p.add_argument("--packet", required=True)
    p.add_argument("--question-text", default=None)
    p.add_argument("--evidence-inventory", default=None)
    p.add_argument("--transport", default=None,
                   choices=["codex"])
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("evaluate")
    p.add_argument("--results", nargs="+", required=True)
    p.add_argument("--challenger-arm", default="B")
    p.add_argument("--baseline-arm", default="A")
    p.add_argument("--evidence-class", required=True,
                   choices=list(rre.JUDGMENT_EVIDENCE_CLASSES))
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("demo")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("verify-freeze")
    p.set_defaults(func=cmd_verify_freeze)

    p = sub.add_parser("freeze-receipt")
    p.add_argument("--out", default=None)
    p.add_argument("--timestamp",
                   default=__import__("time").strftime(
                       "%Y-%m-%dT%H:%M:%S"))
    p.set_defaults(func=cmd_freeze_receipt)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
