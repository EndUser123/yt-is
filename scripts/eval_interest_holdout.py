"""ISEM v1 CLI — Interest semantic evaluator against the v1.1 holdout.

Subcommands:
  support          deterministic evidence-cluster needle probe (no labels
                   content printed; per-label supporting cluster ids only)
  score            run the frozen evaluator; verifies the sealed holdout
                   sha256 and the FROZEN_MANIFEST before touching it
  stability        emit preregistered perturbation manifests + variant
                   inventories for post-freeze Arm-B re-runs
  freeze-receipt   recompute artifact hashes into FREEZE_RECEIPT.json

The private holdout path is passed with --gt ONLY at score time, after
the inference implementation is frozen elsewhere. This tool refuses to
run `score` unless --allow-holdout is passed explicitly (guard against
accidental early opening).

BINDING_AMENDMENT_2 (review finding F1): `support` now carries the same
explicit holdout authorization boundary as `score`. A --gt file that
lives under the private holdout directory — or that hashes to the
sealed holdout digest — is refused without --allow-holdout. The private
directory check is purely path-based (the sealed file's bytes are never
read without authorization); the digest check catches relocated copies
of the sealed artifact.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef import eval_interest_semantic as isem  # noqa: E402

DEFAULT_MANIFEST = REPO / ("docs/handoffs/interest-intelligence/"
                           "interest-semantic-evaluator-v1/"
                           "FREEZE_RECEIPT.json")

# F1 authorization boundary: the sealed real GT lives here.
PRIVATE_GT_DIR = Path("P:/.data/yt-is/private")


def _requires_holdout_authorization(gt_path: Path) -> bool:
    """True when opening gt_path needs explicit --allow-holdout.

    Path-based check FIRST so the sealed real artifact is never read
    during the check; digest equality second, catching a copy of the
    sealed artifact stored outside the private directory.
    """
    try:
        if gt_path.resolve().is_relative_to(
                PRIVATE_GT_DIR.resolve()):
            return True
    except (OSError, ValueError):
        pass
    if gt_path.exists():
        return isem.sha256_file(gt_path) == isem.SEALED_GT_SHA256
    return False


def cmd_support(args) -> int:
    if _requires_holdout_authorization(Path(args.gt)) \
            and not args.allow_holdout:
        raise SystemExit(
            "refusing to open holdout without --allow-holdout")
    from ef.evidence_clusters import cached_clusters
    clusters, _coverage = cached_clusters()
    cluster_texts = {}
    for c in clusters:
        reps = " ".join(r.get("title", "") or ""
                        for r in (c.get("representative") or []))
        terms = " ".join(c.get("terms") or [])
        ents = " ".join(e.get("entity", "") or ""
                        for e in (c.get("entities") or [])[:8])
        label = c.get("label") or ""
        cluster_texts[c["cluster_id"]] = \
            f"{label} {terms} {ents} {reps}"
    gt = isem.load_ground_truth(args.gt)
    out = {"generated": isem.time.strftime("%Y-%m-%dT%H%M%S"),
           "eligible_cluster_ids": [c["cluster_id"] for c in clusters],
           "support_by_label_id": {}}
    for lab in gt["labels"]:
        hits = isem.needle_support(lab, cluster_texts)
        out["support_by_label_id"][lab["label_id"]] = hits[:50]
    Path(args.out).write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    summary = {lid: len(v)
               for lid, v in out["support_by_label_id"].items()}
    print(json.dumps({"wrote": args.out,
                      "support_counts": summary}))
    return 0


def _load_support(path):
    if not path:
        return None, None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    eligible = set(data.get("eligible_cluster_ids") or [])
    return eligible, data.get("support_by_label_id") or {}


class OfflineStubJudge:
    """Deterministic offline judge for fixtures/tests ONLY.

    Rule: match iff normalized substring containment either way between
    candidate text+context and target name/aliases (len>=4 needles).
    Never used for a formal run; its use stamps every report.
    """

    live = False


def make_judge(name, cache):
    if name == "stub":
        def stub(prompt_text, surface, target):
            needles = [isem.normalize_text(target["canonical_name"])]
            needles += [isem.normalize_text(a)
                        for a in target.get("aliases", [])
                        if len(a.strip()) >= 4]
            hay = isem.normalize_text(
                f"{surface['text']} | {surface['context']}")
            return any(len(n) >= 4 and n in hay for n in needles)
        return stub
    if name == "codex":
        return isem.judge_transport_factory(cache_path=cache)
    raise SystemExit(f"unknown judge: {name}")


def cmd_score(args) -> int:
    manifest_path = Path(args.manifest or DEFAULT_MANIFEST)
    verify_manifest(manifest_path)

    gt = isem.load_ground_truth(args.gt)
    # AMENDMENT_3: the generic arbitrary-result score path must never
    # touch the sealed v1.1 holdout, even with --allow-holdout. Only
    # the formal bound-three execution surface may score it.
    if gt["sealed_sha256"] == isem.SEALED_GT_SHA256:
        raise SystemExit(
            "refusing generic score against the sealed v1.1 holdout: "
            "use the formal bound runner scripts/run_sealed_isem_d3.py")
    isem.verify_sealed(gt)

    eligible, support_by_label = _load_support(args.support)

    payload = json.loads(Path(args.result).read_text(encoding="utf-8"))
    judge = make_judge(args.judge, args.judge_cache)

    report = isem.evaluate(
        gt, payload, judge,
        eligible_cluster_ids=eligible,
        support_hits_by_label=support_by_label,
        stability_results=bool(args.stability_seen))

    stamped = dict(report)
    stamped["judge"] = {"kind": args.judge,
                        "model": isem.JUDGE_MODEL,
                        "live": args.judge != "stub"}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stamped, indent=2), encoding="utf-8")
    print(json.dumps(_brief({"overall_verdict":
                             stamped["overall_verdict"],
                             "tracks": stamped["tracks"]}), indent=2))
    return 0


def cmd_stability_check(args) -> int:
    """Compare matched sets of the base report vs variant reports."""
    base = json.loads(Path(args.base).read_text(encoding="utf-8"))
    variants = {}
    spec = json.loads(Path(args.variants_index).read_text(
        encoding="utf-8"))
    entries = spec["variant_reports"] if "variant_reports" in spec \
        else [{"scheme": Path(p).stem.replace("report-", ""),
               "report": p} for p in spec] if isinstance(spec, list) \
        else None
    if entries is None:
        raise SystemExit("variants index: unrecognized shape")
    for e in entries:
        name, rep_path = e["scheme"], e["report"]
        variants[name] = json.loads(
            Path(rep_path).read_text(encoding="utf-8"))
    # scored reports nest nothing; evaluate() output holds tracks directly
    from ef.eval_interest_semantic import compare_matched_sets
    comparison = compare_matched_sets(base, variants)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"base_report": str(args.base),
               "schemes": {k: v["stable"] for k, v in
                           comparison.items()},
               "detail": comparison,
               "all_stable": all(v["stable"] for v in
                                 comparison.values())}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["schemes"], indent=2))
    return 0


def _brief(tracks_blob) -> dict:
    out = {"overall_verdict": tracks_blob.get("overall_verdict"),
           "tracks": {}}
    for cls, tm in (tracks_blob.get("tracks") or {}).items():
        out["tracks"][cls] = {
            "n_scorable_positives": tm["n_scorable_positives"],
            "recall_gross": tm["recall_gross"],
            "recall_provenance_ok": tm["recall_provenance_ok"],
            "unsupported_matched_hits":
                tm["unsupported_matched_hits"],
            "interest_negative_fp_hits":
                tm["interest_negative_fp_hits"],
            "verdict": tm["verdict"],
        }
    return out


def verify_manifest(manifest_path: Path) -> None:
    if not manifest_path.exists():
        print(f"FATAL: frozen manifest missing: {manifest_path}",
              file=sys.stderr)
        raise SystemExit(2)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    repo_root = REPO
    for entry in manifest.get("frozen_artifacts", []):
        p = repo_root / entry["path"]
        if not p.exists():
            problems.append(f"missing frozen artifact {entry['path']}")
            continue
        digest = isem.sha256_file(p)
        if digest != entry["sha256"]:
            problems.append(
                f"hash drift {entry['path']}: "
                f"{digest[:12]} != {entry['sha256'][:12]}")
    if problems:
        print("FATAL: evaluator artifacts drifted since freeze:",
              file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
        raise SystemExit(3)


def cmd_stability(args) -> int:
    """Emit variant inventories + manifests without calling providers."""
    from ef.evidence_clusters import evidence_cluster_inventory
    inv = evidence_cluster_inventory() if not args.inventory else \
        json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    built = isem.stability_variants(inv)
    root = Path(args.artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    index = []
    for name, var_inv in built["variants"].items():
        vp = root / f"inventory-{name}.json"
        vp.write_text(json.dumps(var_inv, indent=1), encoding="utf-8")
        index.append({"scheme": name, "inventory": str(vp)})
    mp = root / "manifests.json"
    mp.write_text(json.dumps(index + [{"manifests": built["manifests"]}],
                             indent=2), encoding="utf-8")
    print(json.dumps({"artifact_root": str(root),
                      "schemes": [i["scheme"] for i in index]}))
    return 0


def cmd_freeze_receipt(args) -> int:
    paths = [
        "ef/eval_interest_semantic.py",
        "scripts/eval_interest_holdout.py",
        "tests/test_eval_interest_semantic.py",
        "ef/isem_d3_binding.py",
        "ef/sealed_execution.py",
        "scripts/run_sealed_isem_d3.py",
        "scripts/materialize_d3_contestants.py",
        "docs/handoffs/interest-intelligence/"
        "interest-semantic-evaluator-v1/"
        "METRIC_PLAN_PREREGISTRATION.md",
    ]
    frozen = []
    for rel in paths:
        p = REPO / rel
        if not p.exists():
            raise SystemExit(f"frozen path missing: {rel}")
        frozen.append({"path": rel.replace("\\", "/"),
                       "sha256": isem.sha256_file(p)})
    receipt = {
        "receipt": "isem_v1_freeze",
        "agent": "zcode",
        "host": "zcode",
        "created_utc": isem.time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": args.status,
        "candidate_inference_implementation":
            args.candidate_implementation,
        "sealed_holdout_public_hash_echo": isem.SEALED_GT_SHA256,
        "preregistration_doc":
            "docs/handoffs/interest-intelligence/"
            "interest-semantic-evaluator-v1/"
            "METRIC_PLAN_PREREGISTRATION.md",
        "judge_prompts_sha256": {
            "positive": isem.sha256_bytes(
                isem.FROZEN_JUDGE_PROMPT_POSITIVE.encode("utf-8")),
            "negative_interest": isem.sha256_bytes(
                isem.FROZEN_JUDGE_PROMPT_NEGATIVE_INTEREST.encode(
                    "utf-8"))},
        "judge_model_config": {
            "model": isem.JUDGE_MODEL,
            "reasoning_effort": isem.JUDGE_REASONING_EFFORT,
            "timeout_s": isem.JUDGE_TIMEOUT_S,
            "max_attempts": isem.JUDGE_MAX_ATTEMPTS},
        "min_n_per_type": isem.MIN_N_PER_TYPE,
        "stability_schemes": [
            "S1_RANDOM_DROP_5PCT(seed1337,min8)",
            "S2_TOP_BREADTH_DROP_10",
            "S3_REPS_TRIM(first2)",
            "S4_ORDER_SHUFFLE(seed20260826)"],
        "frozen_artifacts": frozen,
        "gate_unseal_preconditions": [
            "architect publishes selected inference implementation SHA",
            "this same evaluator binds --inference-sha and re-verifies"
            " manifest hashes unchanged",
            "only then may --gt point at the sealed v1.1 artifact"],
        "post_run_constraints": [
            "single scoring run; no inference tuning afterward",
            "exact per-item outcomes reported regardless of verdicts"],
    }
    if args.note:
        receipt["amendment_note"] = args.note
    dst = Path(args.out or DEFAULT_MANIFEST)
    dst.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"receipt": dst.as_posix(),
                      "artifacts": len(frozen),
                      "status": receipt["status"]}))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("support")
    s.add_argument("--gt", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--allow-holdout", action="store_true",
                   help="F1: required when --gt is the sealed holdout "
                        "or lives under the private holdout directory")
    s.set_defaults(fn=cmd_support)

    s = sub.add_parser("score")
    s.add_argument("--gt", required=True)
    s.add_argument("--result", required=True)
    s.add_argument("--support", default=None)
    s.add_argument("--judge", choices=["codex", "stub"],
                   default="codex")
    s.add_argument("--judge-cache", default=None)
    s.add_argument("--stability-seen", action="store_true",
                   help="set when re-scoring after stability runs; "
                        "marks the Interest PASS criterion complete")
    s.add_argument("--out", required=True)
    s.add_argument("--manifest", default=None)
    s.add_argument("--allow-holdout", action="store_true")
    s.set_defaults(fn=lambda a: (
        SystemExit("refusing to open holdout without --allow-holdout")
        if not a.allow_holdout else cmd_score(a)))

    c = sub.add_parser("stability-check")
    c.add_argument("--base", required=True,
                   help="base-run scored report JSON")
    c.add_argument("--variants-index", required=True,
                   help='JSON: [{"scheme": name, "report": path}, ...]'
                        ' or {"variant_reports": [same]}')
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_stability_check)

    t = sub.add_parser("stability")
    t.add_argument("--artifact-root", required=True)
    t.add_argument("--inventory", default=None)
    t.set_defaults(fn=cmd_stability)

    f = sub.add_parser("freeze-receipt")
    f.add_argument("--out", default=None)
    f.add_argument("--status", default="EVALUATOR_READY_WAITING_ON_"
                                      "INFERENCE_FREEZE")
    f.add_argument("--candidate-implementation", default="NOT_YET_FROZEN")
    f.add_argument("--note", default=None,
                   help="optional amendment note recorded in the receipt")
    f.set_defaults(fn=cmd_freeze_receipt)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
