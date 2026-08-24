r"""Run all content syncs and generate a digest. Designed for scheduled execution.

Runs: YouTube channel sync → Reddit sync → Hacker News sync → Discord sync
(optional, needs DISCORD_BOT_TOKEN) → daily digest.

Usage:
    python scripts/run_all_syncs.py           # run everything
    python scripts/run_all_syncs.py --quick   # skip YouTube scan (just Reddit + HN + digest)

Scheduled task example:
    Register-ScheduledTask -TaskName "YtisContentSync" -Trigger (New-ScheduledTaskTrigger -Daily -At "06:00") \
        -Action (New-ScheduledTaskAction -Execute "python" -Argument "P:\packages\yt-is\scripts\run_all_syncs.py --quick")
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Bootstrap BEFORE any csf import: scheduled tasks run with cwd=System32,
# where a stale workspace-level csf copy (P:\__csf) on sys.path would
# otherwise shadow this repo's csf and die inside its cks import.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def run_script(name, script_path, timeout=3600):
    """Run a script and return (success, output_summary)."""
    print(f"\n{'='*60}")
    print(f"  Running: {name}")
    print(f"{'='*60}\n")

    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(REPO),
            timeout=timeout,
        )
        elapsed = time.monotonic() - start
        if result.returncode == 0:
            status = "✓"
        elif result.returncode == 2 and "YouTube" in name:
            # intake guard exit-2: another pipeline instance owns the run
            # (a detached drain, or the 06:00 task) — the work IS being
            # done, not failing. Healthy defer, not a red flag.
            status = "⏭ (already running elsewhere — deferred)"
            print(f"\n  {status} {name} — {elapsed:.0f}s")
            return True
        else:
            status = f"✗ (exit {result.returncode})"
        print(f"\n  {status} {name} — {elapsed:.0f}s")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"\n  ✗ {name} — timed out after {timeout}s")
        return False
    except KeyboardInterrupt:
        print(f"\n  ⏸ {name} — interrupted (progress saved)")
        return False


def run_script_threaded(name, script_path, timeout, results, prefix=""):
    """run_script in a worker thread; connector output interleaves into
    the log, each block headed with the connector name."""
    import subprocess as _sp
    import time as _time
    header = "=" * 60
    print("\n" + header + "\n  Starting: " + name + "\n" + header + "\n",
          flush=True)
    start = _time.monotonic()
    try:
        result = _sp.run(
            [sys.executable, str(script_path)],
            cwd=str(REPO), timeout=timeout,
        )
        elapsed = _time.monotonic() - start
        if result.returncode == 2 and "YouTube" in name:
            # intake guard: another instance owns the run — healthy defer
            print("\n  [%s] DEFERRED (already running elsewhere) — %.0fs"
                  % (name, elapsed), flush=True)
            results[name] = True
            return
        mark = "OK" if result.returncode == 0 else ("exit %d" % result.returncode)
        print("\n  [%s] %s — %.0fs" % (name, mark, elapsed), flush=True)
        results[name] = result.returncode == 0
    except _sp.TimeoutExpired:
        print("\n  [%s] TIMED OUT after %ss" % (name, timeout), flush=True)
        results[name] = False
    except Exception as e:
        print("\n  [%s] %s: %s" % (name, type(e).__name__, str(e)[:120]),
              flush=True)
        results[name] = False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run all content syncs")
    parser.add_argument("--quick", action="store_true",
                        help="Skip YouTube scan (just Reddit + HN + digest)")
    parser.add_argument("--skip-youtube", action="store_true")
    parser.add_argument("--skip-reddit", action="store_true")
    parser.add_argument("--skip-hn", action="store_true")
    parser.add_argument("--skip-discord", action="store_true")
    parser.add_argument("--skip-digest", action="store_true")
    parser.add_argument("--skip-trend-alerts", action="store_true",
                        help="Skip the daily trend-alert computation "
                             "(compute_trend_alerts.py)")
    parser.add_argument("--skip-exclusion-policy", action="store_true",
                        help="Skip the exclusion-policy guardrail "
                             "(enforce_exclusion_policy.py). Discouraged: "
                             "without this, excluded-category channels "
                             "may be re-fetched.")
    args = parser.parse_args(argv)

    print("ytis — Content Sync")
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    results = {}

    # Exclusion-policy guardrail. Runs FIRST so every connector that
    # follows sees a blocklist that's already in sync with the operator's
    # current excluded_categories. The script is idempotent: missing
    # blocks get added, stale ones (channel reclassified, ★ exception
    # added) get removed. The operator's policy is "excluded means
    # blocked, no separate approval" — this is the line that enforces it
    # even on days when no review export is applied.
    if not args.skip_exclusion_policy:
        from csf.paths import get_batch_db_path
        from scripts.enforce_exclusion_policy import enforce as enforce_policy
        try:
            settings_path = REPO / "config" / "discovery-settings.json"
            excluded: list[str] = []
            if settings_path.is_file():
                _s = json.loads(settings_path.read_text(encoding="utf-8"))
                excluded = list(_s.get("excluded_categories") or [])
            if excluded:
                _r = enforce_policy(
                    db_path=get_batch_db_path(),
                    excluded_categories=frozenset(excluded),
                )
                results['exclusion_policy'] = {
                    "promoted": _r.get("promoted", 0),
                    "reconciled": _r.get("reconciled", 0),
                    "errors": _r.get("errors", []),
                }
                print(f"\n  exclusion_policy: "
                      f"promoted={results['exclusion_policy']['promoted']}, "
                      f"reconciled={results['exclusion_policy']['reconciled']}")
            else:
                results['exclusion_policy'] = {"status": "skipped",
                                               "reason": "no excluded categories"}
        except Exception as exc:
            results['exclusion_policy'] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"\n  ✗ exclusion_policy: {results['exclusion_policy']['error']}")

    # PARALLEL PHASE 1: YouTube is the heavyweight (hours); every light
    # connector runs concurrently beside it. All connectors are
    # lock-retry hardened, so shared-DB contention degrades to waits.
    from concurrent.futures import ThreadPoolExecutor

    youtube_job = None
    if not args.quick and not args.skip_youtube:
        youtube_job = ("YouTube Channel Sync",
                       REPO / "scripts" / "run_intake_pipeline.py",
                       21600)   # backlog drains can take hours; the task
                                # starts 06:00 and has the whole day
    light_jobs = []
    if not args.skip_reddit:
        light_jobs.append(("reddit", REPO / "scripts" / "run_reddit_sync.py",
                           1800))
    if not args.skip_hn:
        light_jobs.append(("hn", REPO / "scripts" / "run_hn_sync.py", 600))
        light_jobs.append(("rss", REPO / "scripts" / "run_rss_sync.py",
                           5400))   # twitter pacing: 12 accts x 75s
        light_jobs.append(("github", REPO / "scripts" / "run_github_sync.py",
                           900))
    light_jobs.append(("dht_ingest", REPO / "scripts" / "run_dht_ingest.py",
                       300))

    with ThreadPoolExecutor(max_workers=1 + len(light_jobs)) as pool:
        futures = []
        if youtube_job:
            name, path, timeout = youtube_job
            futures.append(pool.submit(run_script_threaded, name, path,
                                       timeout, results, "yt"))
        for name, path, timeout in light_jobs:
            futures.append(pool.submit(run_script_threaded, name, path,
                                       timeout, results, name))
        for fut in futures:
            fut.result()   # ALL of phase 1 completes before indexing

    if not args.skip_discord:
        import os
        from csf.paths import load_workspace_env
        load_workspace_env()
        if os.environ.get("DISCORD_BOT_TOKEN"):
            results['discord'] = run_script(
                "Discord Sync",
                REPO / "scripts" / "run_discord_sync.py",
                timeout=600,
            )
        else:
            print("\n  – Discord skipped (no DISCORD_BOT_TOKEN in P:/.env)")

    # Make new connector batches searchable (embeds into the Evidence Fabric)
    results['ef_ingest'] = run_script(
        "EF Connector Ingest",
        REPO / "scripts" / "run_connector_ingest.py",
        timeout=1800,
    )

    # Keep topic clusters fresh: assign new chunks to nearest centroid
    results['topic_assign'] = run_script(
        "Topic Assignment",
        REPO / "scripts" / "run_topic_assignment.py",
        timeout=1800,
    )

    # Compute and persist today's trend alerts (topic-momentum thresholds)
    # so the /home panel and daily digest have today's data to surface.
    if not args.skip_trend_alerts:
        results['trend_alerts'] = run_script(
            "Trend Alerts",
            REPO / "scripts" / "compute_trend_alerts.py",
            timeout=300,
        )

    # Self-heal: fill missing channel metadata (thumbnail/description,
    # batched 50-per-call; costs 0 units once complete)
    results['channel_metadata'] = run_script(
        "Channel Metadata Backfill",
        REPO / "scripts" / "backfill_channel_metadata.py",
        timeout=300,
    )

    # Self-heal: fill titles for videos enqueued without one (oEmbed, paced)
    results['title_backfill'] = run_script(
        "Title Backfill",
        REPO / "scripts" / "backfill_titles.py",
        timeout=600,
    )

    if not args.skip_digest:
        results['digest'] = run_script(
            "Daily Digest",
            REPO / "scripts" / "generate_digest.py",
            timeout=60,
        )

    # Summary
    print(f"\n{'='*60}")
    print("  Summary")
    print(f"{'='*60}\n")

    all_ok = True
    for name, ok in results.items():
        status = "✓" if ok else "✗"
        print(f"  {status} {name}")
        if not ok:
            all_ok = False

    print(f"\n{'All syncs completed' if all_ok else 'Some syncs failed'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
