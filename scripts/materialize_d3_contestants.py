"""Materialize the three frozen D3 contestants to a durable,
content-addressed, non-session-scoped store (AMENDMENT_3 item B).

Replays DETERMINISTIC_ASSEMBLY_REPLAY_V1 (ef/isem_d3_binding.py) over
the frozen provider artifacts, writes the exact canonical bytes under

    P:/.data/yt-is/ef/interest-inference/frozen-contestants/isem-d3-v1/
        <payload_sha256>.json

plus a materialization manifest binding each contestant's logical id,
canonical payload sha256, byte length, storage path, D3 freeze commit,
implementation manifest hash, reconstruction version, and strict-
validator status. A byte-identical mirror of the manifest is written
into the repository (durable, git-tracked) while the payload files
live in the established yt-is data home alongside the run corpus they
were reconstructed from.

Run BEFORE holdout unseal. Contestants are NEVER regenerated after
unseal; formal scoring re-hashes the stored bytes before every use.
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
from ef import isem_d3_binding as bind  # noqa: E402

DEFAULT_OUT_DIR = Path(
    "P:/.data/yt-is/ef/interest-inference/frozen-contestants/isem-d3-v1")
DEFAULT_MIRROR = REPO / (
    "docs/handoffs/interest-intelligence/"
    "isem-d3-contestant-materialization.json")
FREEZE_DOC = REPO / ("docs/handoffs/interest-intelligence/"
                     "inference-candidate-d3-freeze.json")


def materialize(out_dir: Path, mirror_path: Path,
                freeze_path: Path) -> dict:
    rep = bind.verify_binding(REPO, freeze_path)
    recipe = rep["reconstruction"]["serialization_recipe"]
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for c in rep["contestants"]:
        expected = c["expected_sha256"]
        # Recompute the exact bytes under the matched recipe from the
        # validated replay result: reconstruct once more for the byte
        # stream (cheap, deterministic) so the file IS the payload.
        assembled, stats = bind.reconstruct_contestant(
            c["run_root"], bind.load_freeze(freeze_path))
        variants = bind.canonical_serialization_variants(assembled)
        blob = next(v for name, v in variants.items()
                    if isem.sha256_bytes(v) == expected
                    and name == recipe)
        digest = isem.sha256_bytes(blob)
        if digest != expected:
            raise bind.BindingRefusal(
                "CONTESTANT_RECONSTRUCTION_MISMATCH",
                f"{c['run_id']}: materialized {digest} != {expected}")
        dst = out_dir / f"{digest}.json"
        if dst.exists():
            existing = dst.read_bytes()
            if existing != blob:
                raise bind.BindingRefusal(
                    "MATERIALIZATION_CONFLICT",
                    f"{dst} exists with different bytes")
        else:
            dst.write_bytes(blob)
        entries.append({
            "run_id": c["run_id"],
            "payload_sha256": digest,
            "byte_length": len(blob),
            "storage_path": str(dst),
            "d3_freeze_commit": "f7bd24fdb917aa5e35112d0b2f2eae1c2129bf59",
            "implementation_manifest_sha256":
                rep["inference_freeze"]["implementation_manifest_sha256"],
            "reconstruction_version": bind.RECONSTRUCTION_PROCEDURE,
            "reconstruction_recipe": recipe,
            "strict_validator_status": "PASSED",
            "counts": c["counts"],
        })
    manifest = {
        "document_kind": "ISEM_D3_CONTESTANT_MATERIALIZATION",
        "created_utc": isem.time.strftime("%Y-%m-%dT%H:%M:%S"),
        "store_root": str(out_dir),
        "holdout_state": "SEALED — contestants materialized pre-unseal",
        "note": "Content-addressed canonical payload bytes; formal "
                "scoring re-hashes bytes immediately before use; "
                "contestants are never regenerated after unseal.",
        "binding_identity_sha256": rep["binding_identity_sha256"],
        "contestants": entries,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_bytes = json.dumps(manifest, indent=1,
                                ensure_ascii=False).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    mirror_path.write_bytes(manifest_bytes)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--freeze", default=str(FREEZE_DOC))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--mirror", default=str(DEFAULT_MIRROR))
    a = ap.parse_args(argv)
    try:
        manifest = materialize(Path(a.out_dir), Path(a.mirror),
                               Path(a.freeze))
    except bind.BindingRefusal as exc:
        print(f"MATERIALIZATION REFUSED: {exc.code}: {exc.detail}",
              file=sys.stderr)
        return 2
    print(json.dumps({
        "store_root": manifest["store_root"],
        "manifest": str(Path(a.out_dir) / "manifest.json"),
        "mirror": a.mirror,
        "contestants": [
            {"run_id": e["run_id"], "sha256": e["payload_sha256"],
             "bytes": e["byte_length"]} for e in manifest["contestants"]],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
