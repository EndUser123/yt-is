#!/usr/bin/env python3
"""One repeatable command for the discovery→classify→exclude→sync cycle.

Chains the existing, individually-tested steps so the whole "find new
channels from Watch Later / History, classify them, exclude unwanted
categories, and sync" workflow runs as a single invocation:

  1. refresh ~/youtube_cookies.txt from the configured browser profile
  2. csf-source watchlater (dry-run, then import when auto_import)
  3. csf-source history   (dry-run, then import when auto_import)
  4. csf-source categorize (idempotent; Gemini CLI)
  5. promote_excluded_categories --apply (only when categories configured)
  6. yt-is sync (only when run_sync)

Every step's raw stdout/stderr and a step receipt land in
.logs/discovery_cycle/<timestamp>/ so a run is auditable and resumable —
all underlying steps are idempotent, so re-running after a failure
re-does only what is left.

Configuration: config/discovery-settings.json (see
config/discovery-settings.example.json). The real file is gitignored;
the example is committed.

Exit codes: 0 all steps ok; 1 any step failed (receipt says which);
2 configuration error.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import get_ytis_log_root, load_workspace_env

DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "discovery-settings.json"


def build_page_path() -> Path:
    from csf.paths import get_ytis_log_root

    return get_ytis_log_root() / "channel_review" / "review.html"

REQUIRED_KEYS = (
    "cookies_browser",
    "auto_import",
    "min_watchlater_videos",
    "min_history_videos",
    "categorize_workers",
    "excluded_categories",
    "run_sync",
)


def load_settings(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(
            f"settings file not found: {path} — copy config/discovery-settings.example.json "
            "to config/discovery-settings.json and adjust"
        )
    settings = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_KEYS if key not in settings]
    if missing:
        raise ValueError(f"settings file {path} is missing keys: {missing}")
    if not isinstance(settings["excluded_categories"], list):
        raise ValueError("excluded_categories must be a list")
    return settings


def _run_step(
    name: str,
    cmd: list[str],
    run_dir: Path,
) -> dict[str, object]:
    """Run one subprocess step, capture output, and write a receipt."""
    print(f"[discovery-cycle] {name}: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    (run_dir / f"{name}.stdout.log").write_text(result.stdout, encoding="utf-8")
    if result.stderr:
        (run_dir / f"{name}.stderr.log").write_text(result.stderr, encoding="utf-8")
    receipt = {
        "step": name,
        "command": cmd,
        "returncode": result.returncode,
        "ok": result.returncode == 0,
    }
    print(
        f"[discovery-cycle] {name}: {'ok' if receipt['ok'] else f'FAILED (exit {result.returncode})'}"
        + ("" if receipt["ok"] else " — see receipt logs in run dir")
    )
    return receipt


def run_cycle(
    settings: dict[str, object],
    run_dir: Path,
    *,
    skip_sync: bool = False,
    allow_spend: bool = False,
    open_review_page: bool = False,
) -> tuple[bool, dict[str, object]]:
    from csf.categorize import CATEGORIES

    cookies_browser = str(settings["cookies_browser"])
    auto_import = bool(settings["auto_import"])
    excluded = [str(c) for c in settings["excluded_categories"]]
    run_sync = bool(settings["run_sync"]) and not skip_sync

    steps: list[dict[str, object]] = []
    ok = True

    def step(name: str, cmd: list[str]) -> bool:
        nonlocal ok
        receipt = _run_step(name, cmd, run_dir)
        steps.append(receipt)
        if not receipt["ok"]:
            ok = False
        return receipt["ok"]

    # 1. Refresh the exported cookie file from the browser (the discovery
    #    steps read the live browser store directly, but keeping the file
    #    fresh preserves the file-based fallback for other consumers).
    refresh = _run_step(
        "refresh_cookies",
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "refresh_youtube_cookies.py"),
            "--browser",
            cookies_browser,
        ],
        run_dir,
    )
    steps.append(refresh)
    if not refresh["ok"]:
        ok = False

    # 2-3. Discovery: dry-run always (receipt for review), import when auto_import.
    # --allow-spend is per-run by design (the guard dies with the process);
    # the cycle propagates the operator's explicit authorization only.
    for kind, min_flag, min_value in (
        ("watchlater", "--min-watchlater-videos", int(settings["min_watchlater_videos"])),
        ("history", "--min-history-videos", int(settings["min_history_videos"])),
    ):
        base = [
            sys.executable,
            str(REPO_ROOT / "bin" / "csf-source"),
            # --allow-spend is a top-level flag and must precede the subcommand
            *(["--allow-spend"] if allow_spend else []),
            kind,
            "--cookies-from-browser",
            cookies_browser,
            min_flag,
            str(min_value),
        ]
        if not step(f"{kind}_dryrun", base + ["--dry-run"]):
            continue  # dry-run failed; importing would be blind
        if auto_import:
            step(f"{kind}_import", base)

    # 4. Classification (idempotent).
    if ok:
        step(
            "categorize",
            [
                sys.executable,
                str(REPO_ROOT / "bin" / "csf-source"),
                "categorize",
                "--workers",
                str(int(settings["categorize_workers"])),
            ],
        )

    # 5. Category exclusion promotion (only when configured; soft-block only).
    if ok and excluded:
        unknown = [c for c in excluded if c not in CATEGORIES]
        if unknown:
            print(
                f"[discovery-cycle] excluded_categories has unknown names {unknown}; "
                "valid: " + ", ".join(CATEGORIES),
                file=sys.stderr,
            )
            return False, {"steps": steps, "error": f"unknown categories: {unknown}"}
        step(
            "promote_excluded",
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "promote_excluded_categories.py"),
                "--exclude",
                ",".join(excluded),
                "--apply",
            ],
        )
    elif not excluded:
        steps.append({"step": "promote_excluded", "skipped": "no excluded_categories configured"})

    # 6. Sync (long; 30-90 min at 2,600+ channels).
    if ok and run_sync:
        step("sync", [sys.executable, str(REPO_ROOT / "bin" / "yt-is"), "sync"])
    elif not run_sync:
        steps.append({"step": "sync", "skipped": "run_sync false or --skip-sync"})

    # 7. Review page: always build after classification so the operator's
    # manual pass is one double-click away. NEVER auto-open: the default-open
    # popped the operator's browser uninvited (2026-08-25); opening is a
    # deliberate act (--open or settings "open_review_page": true).
    if ok and bool(settings.get("build_review_page", True)):
        build = _run_step(
            "build_review_page",
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "build_channel_review_page.py"),
                "--excluded",
                ",".join(excluded),
            ],
            run_dir,
        )
        steps.append(build)
        if build["ok"] and open_review_page and bool(settings.get("open_review_page", False)):
            import subprocess as _sp

            try:
                _sp.run(
                    ["cmd", "/c", "start", "", build_page_path()], check=False,
                    capture_output=True,
                )
            except OSError:
                pass  # opening a browser is best-effort only

    return ok, {"steps": steps}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS_PATH,
        help=f"Settings JSON (default: {DEFAULT_SETTINGS_PATH})",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Run discovery/classify/promote but not the long sync step.",
    )
    parser.add_argument(
        "--allow-spend",
        action="store_true",
        help="Authorize YouTube Data API spending for this cycle's discovery steps (per-run, dies with the process).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Also open the built review page in the browser (default: never auto-open).",
    )
    args = parser.parse_args(argv)

    # API keys etc. — same loader csf-source uses (explicit shell env wins).
    load_workspace_env()

    try:
        settings = load_settings(args.settings)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = get_ytis_log_root() / "discovery_cycle" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    ok, report = run_cycle(
        settings,
        run_dir,
        skip_sync=args.skip_sync,
        allow_spend=args.allow_spend,
        open_review_page=args.open,
    )
    report["created_at"] = datetime.now(timezone.utc).isoformat()
    report["settings_path"] = str(args.settings)
    report["run_dir"] = str(run_dir)
    report_path = run_dir / "cycle_receipt.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[discovery-cycle] receipt: {report_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
