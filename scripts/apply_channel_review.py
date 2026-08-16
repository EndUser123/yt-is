#!/usr/bin/env python3
"""Apply a review page's decisions: manual category assignments + exclusions.

Consumes the review_decisions.json exported by the channel review page
(scripts/build_channel_review_page.py):

1. Every manual category assignment is stored via upsert_channel (only the
   changed channels — the page exports just the deltas).
2. The excluded-categories list is written into
   config/discovery-settings.json so future discovery cycles honor it.
3. Excluded categories are promoted to the blocklist through the existing
   promotion script (dry-run receipt by default, --apply-promotion to block).

Exit codes: 0 ok; 1 apply failure; 2 input/config error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import block_channel, unblock_channel, upsert_channel
from csf.categorize import CATEGORIES, OTHER_CATEGORY
from csf.paths import get_batch_db_path

SETTINGS_PATH = REPO_ROOT / "config" / "discovery-settings.json"
VALID = set(CATEGORIES) | {OTHER_CATEGORY}


def apply_decisions(
    decisions: dict[str, object],
    *,
    db_path: Path,
    settings_path: Path,
    apply_promotion: bool,
) -> dict[str, object]:
    assignments = decisions.get("assignments") or {}
    excluded = [str(c) for c in decisions.get("excluded_categories") or []]

    bad_assign = {str(k): v for k, v in assignments.items() if v not in VALID}
    if bad_assign:
        raise ValueError(f"assignments contain invalid categories: {bad_assign}")
    bad_excluded = [c for c in excluded if c not in CATEGORIES]
    if bad_excluded:
        raise ValueError(f"excluded_categories invalid (must be real categories, not Other): {bad_excluded}")

    assigned = 0
    errors: list[dict[str, str]] = []
    for url, category in assignments.items():
        try:
            upsert_channel(
                str(url), db_path=db_path, category=str(category), category_source="manual"
            )
            assigned += 1
        except Exception as exc:
            errors.append({"channel_url": str(url), "error": f"{type(exc).__name__}: {exc}"})

    exempted = 0
    unexempted = 0
    for url in decisions.get("exception_urls") or []:
        try:
            upsert_channel(str(url), db_path=db_path, exempt_from_exclusion=1)
            # An exception takes effect immediately: unblock if it was
            # blocked by the category exclusion it now escapes.
            if unblock_channel(str(url), db_path=db_path):
                pass  # unblocked count tracks explicit unblocks below
            exempted += 1
        except Exception as exc:
            errors.append({"channel_url": str(url), "error": f"exempt failed: {type(exc).__name__}: {exc}"})
    for url in decisions.get("unexception_urls") or []:
        try:
            upsert_channel(str(url), db_path=db_path, exempt_from_exclusion=0)
            unexempted += 1
        except Exception as exc:
            errors.append({"channel_url": str(url), "error": f"unexempt failed: {type(exc).__name__}: {exc}"})

    blocked_count = 0
    unblocked_count = 0
    for url in decisions.get("block_urls") or []:
        try:
            block_channel(str(url), db_path=db_path, reason="operator")
            blocked_count += 1
        except Exception as exc:
            errors.append({"channel_url": str(url), "error": f"block failed: {type(exc).__name__}: {exc}"})
    for url in decisions.get("unblock_urls") or []:
        try:
            unblock_channel(str(url), db_path=db_path)
            unblocked_count += 1
        except Exception as exc:
            errors.append({"channel_url": str(url), "error": f"unblock failed: {type(exc).__name__}: {exc}"})

    settings_updated = False
    if settings_path.is_file():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        if settings.get("excluded_categories") != excluded:
            settings["excluded_categories"] = excluded
            settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            settings_updated = True

    locked_count = 0
    unlocked_count = 0
    for url in decisions.get("lock_urls") or []:
        try:
            upsert_channel(str(url), db_path=db_path, category_source="manual")
            locked_count += 1
        except Exception as exc:
            errors.append({"channel_url": str(url), "error": f"lock failed: {type(exc).__name__}: {exc}"})
    for url in decisions.get("unlock_urls") or []:
        try:
            import sqlite3 as _sq2

            conn2 = _sq2.connect(db_path)
            conn2.execute(
                "UPDATE channel_metadata SET category_source='llm' WHERE channel_url = ?",
                (str(url),),
            )
            conn2.commit()
            conn2.close()
            unlocked_count += 1
        except Exception as exc:
            errors.append({"channel_url": str(url), "error": f"unlock failed: {type(exc).__name__}: {exc}"})

    cleared_count = 0
    for url in decisions.get("clear_category_urls") or []:
        try:
            import sqlite3 as _sq

            conn = _sq.connect(db_path)
            conn.execute(
                "UPDATE channel_metadata SET category=NULL, category_source=NULL "
                "WHERE channel_url = ?",
                (str(url),),
            )
            conn.commit()
            conn.close()
            cleared_count += 1
        except Exception as exc:
            errors.append({"channel_url": str(url), "error": f"clear failed: {type(exc).__name__}: {exc}"})

    reclassified: dict[str, object] = {"status": "skipped", "reason": "none marked"}
    reclassify_urls = [str(u) for u in decisions.get("reclassify_urls") or []]
    if reclassify_urls:
        # Full-evidence reclassification of exactly the marked channels.
        at_file = None
        import tempfile as _tf

        with _tf.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("\n".join(reclassify_urls))
            at_file = fh.name
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "bin" / "csf-source"),
                "--allow-spend",
                "categorize",
                "--channels",
                f"@{at_file}",
                "--workers",
                "3",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        try:
            reclassified = {
                "status": "ok" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "channels": len(reclassify_urls),
                "stdout_tail": result.stdout[-400:],
                "stderr_tail": result.stderr[-200:],
            }
        finally:
            import os as _os

            _os.unlink(at_file)

    promotion: dict[str, object] = {"status": "skipped", "reason": "no excluded categories"}
    if excluded:
        cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "promote_excluded_categories.py"),
            "--exclude",
            ",".join(excluded),
            "--db-path",
            str(db_path),
        ] + (["--apply"] if apply_promotion else [])
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        try:
            promotion = json.loads(result.stdout)
        except json.JSONDecodeError:
            promotion = {
                "status": "error",
                "returncode": result.returncode,
                "stderr": result.stderr[-500:],
            }

    receipt = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "assignments_applied": assigned,
        "exceptions_applied": exempted,
        "exceptions_removed": unexempted,
        "channels_blocked": blocked_count,
        "channels_unblocked": unblocked_count,
        "assignment_errors": errors,
        "excluded_categories": excluded,
        "settings_updated": settings_updated,
        "promotion": promotion,
        "reclassification": reclassified,
        "categories_cleared": cleared_count,
        "locked": locked_count,
        "unlocked": unlocked_count,
    }
    return receipt


def archive_decisions(decisions_path: Path) -> Path | None:
    """Archive the export to the audit trail, then delete the original.

    The decisions file is the operator's decision record (provenance for
    category_source='manual'); keep a timestamped copy under
    .logs/channel_review/applied/ so the workflow's discovery glob never
    re-ingests it while the audit trail survives.
    """
    import shutil

    archive_dir = REPO_ROOT / ".logs" / "channel_review" / "applied"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = archive_dir / f"review_decisions-{stamp}.json"
    shutil.copyfile(decisions_path, destination)
    decisions_path.unlink()
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "decisions", type=Path, nargs="?", default=None,
        help="review_decisions.json from the review page. Omitted: the newest "
        "export is auto-discovered in the standard browser-download and "
        "workspace locations.",
    )
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--settings", type=Path, default=SETTINGS_PATH)
    parser.add_argument(
        "--apply-promotion",
        action="store_true",
        help="Also blocklist excluded categories (default: dry-run receipt only).",
    )
    args = parser.parse_args(argv)

    decisions_path = args.decisions
    if decisions_path is None:
        # Browsers rename repeat downloads "review_decisions (1).json" — glob
        # the pattern, then pick the newest across the standard locations.
        candidates: list[Path] = []
        for directory in (
            Path.home() / "Downloads",
            Path("P:/tmp"),
            Path.cwd(),
        ):
            if directory.is_dir():
                candidates.extend(directory.glob("review_decisions*.json"))
        candidates = sorted(
            (c for c in candidates if c.is_file()), key=lambda c: c.stat().st_mtime
        )
        if not candidates:
            print(
                "error: no decisions file given and none found in "
                + ", ".join(str(c) for c in candidates),
                file=sys.stderr,
            )
            return 2
        decisions_path = candidates[-1]
        print(f"[apply] using newest export: {decisions_path}")
    try:
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {decisions_path}: {exc}", file=sys.stderr)
        return 2
    db_path = args.db_path if args.db_path is not None else get_batch_db_path()
    try:
        receipt = apply_decisions(
            decisions,
            db_path=db_path,
            settings_path=args.settings,
            apply_promotion=args.apply_promotion,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # A2: Exclusion reconciliation — unblock channels whose block reason is
    # a category exclusion that no longer matches their current category.
    # (Gmail DLP pattern: rule no longer applies -> unblock.)
    try:
        import sqlite3 as _sq3

        rconn = _sq3.connect(db_path)
        reconciled = 0
        for burl, reason in rconn.execute(
            "SELECT channel_url, reason FROM channel_blocklist WHERE reason LIKE 'category:%'"
        ).fetchall():
            cat = (reason or "").split(":", 1)[1] if ":" in (reason or "") else ""
            if not cat:
                continue
            row = rconn.execute(
                "SELECT category FROM channel_metadata WHERE channel_url = ?", (burl,)
            ).fetchone()
            current = row[0] if row else None
            if current != cat:
                # Channel's category changed or channel was blacklisted
                # (metadata gone) — the old exclusion reason no longer applies
                rconn.execute(
                    "DELETE FROM channel_blocklist WHERE channel_url = ? AND reason = ?",
                    (burl, reason),
                )
                reconciled += 1
        if reconciled:
            rconn.commit()
        rconn.close()
        receipt["reconciled_blocks"] = reconciled
    except Exception as exc:
        receipt["reconciled_blocks"] = f"error: {type(exc).__name__}: {exc}"

    # Success: archive the export to the audit trail and delete the original
    # so it can never be re-ingested (operator decision record survives in
    # .logs/channel_review/applied/).
    if not receipt["assignment_errors"] and receipt["promotion"].get("status") != "error":
        try:
            archived = archive_decisions(decisions_path)
            print(f"[apply] export archived + removed: {archived}", file=sys.stderr)
        except OSError as exc:
            print(f"[apply] could not remove {decisions_path}: {exc}", file=sys.stderr)

    # The review page is a snapshot of this DB — regenerate it so the
    # operator's browser "Refresh data" shows the applied truth.
    try:
        import subprocess as _sp

        _sp.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "build_channel_review_page.py")],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120,
        )
    except Exception:
        pass  # page rebuild is best-effort; the DB is the source of truth
    print(json.dumps(receipt, indent=2, sort_keys=True))
    if receipt["assignment_errors"] or receipt["promotion"].get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
