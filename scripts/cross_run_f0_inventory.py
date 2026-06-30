"""Path B analyzer: cross-run F0 mechanism inventory.

Walks every sharded-lane run root, extracts:
  - max command-elapsed-s (the F0 mechanism proxy)
  - attempt-1 vs retry event counts and per-attempt maxima
  - cohort hashes for both lanes
  - VPH

Produces a single JSON inventory and prints a top-15 table for human review.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import analyze_command_latency_attribution as A


def _sha10(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:10]


def collect_runs() -> list[Path]:
    roots = sorted(p.parent for p in (ROOT / '.logs/sharded_lane_series').glob('*/sharded_lane_series_summary.json'))
    keep: list[Path] = []
    for root in roots:
        try:
            s = json.loads((root / 'sharded_lane_series_summary.json').read_text(encoding='utf-8'))
        except Exception:
            continue
        if (s.get('worker_shape_signature') or '') != '3+3':
            continue
        if (s.get('run_environment_label') or '').startswith('hotel'):
            continue
        if not s.get('throughput_valid'):
            continue
        pro_cohort = root / 'soak' / 'cohort.a_hominidae_pro.json'
        free_cohort = root / 'soak' / 'cohort.troup_hominidae_free.json'
        if not (pro_cohort.exists() and free_cohort.exists()):
            continue
        keep.append(root)
    return keep


def analyze_one(r: Path) -> dict | None:
    try:
        pkt = A.analyze_run_root(r, 'soak', r.name)
    except Exception as e:  # noqa: BLE001
        return {'name': r.name, 'error': str(e)}
    overall = (pkt.get('overall_rows') or [{}])[0]
    ev = pkt.get('event_attribution') or {}
    attempts = ev.get('attempt_totals') or {}
    summary = pkt.get('summary') or {}
    cohorts = {}
    for cn in ('cohort.a_hominidae_pro.json', 'cohort.troup_hominidae_free.json'):
        cp = r / 'soak' / cn
        if cp.exists():
            cohorts[cn] = _sha10(cp)
    return {
        'name': r.name,
        'vph': summary.get('hot_path_videos_per_hour'),
        'command_total': overall.get('content_fetch_command_elapsed_s_total'),
        'command_max': overall.get('content_fetch_command_elapsed_s_max'),
        'attempt_1_count': (attempts.get('attempt_1') or {}).get('count'),
        'attempt_1_max': (attempts.get('attempt_1') or {}).get('command_elapsed_s_max'),
        'retry_count': (attempts.get('retry') or {}).get('count'),
        'retry_max': (attempts.get('retry') or {}).get('command_elapsed_s_max'),
        'retry_p95': (attempts.get('retry') or {}).get('command_elapsed_s_p95'),
        'retry_total_elapsed': (attempts.get('retry') or {}).get('command_elapsed_s_total'),
        'attempt_1_total_elapsed': (attempts.get('attempt_1') or {}).get('command_elapsed_s_total'),
        'cohort_pro': cohorts.get('cohort.a_hominidae_pro.json'),
        'cohort_free': cohorts.get('cohort.troup_hominidae_free.json'),
    }


def main() -> int:
    runs = collect_runs()
    results: list[dict] = []
    for r in runs:
        rec = analyze_one(r)
        if rec is not None:
            results.append(rec)
    out_path = ROOT / '.logs' / 'sharded_lane_series' / 'cross_run_f0_mechanism_inventory.json'
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding='utf-8')

    f0s = sorted(r.get('command_max') or 0 for r in results)
    n = len(results)
    print(f'runs: {n}')
    if f0s:
        print(
            'F0 max percentiles: '
            f'min={f0s[0]:.1f} '
            f'p25={f0s[n // 4]:.1f} '
            f'p50={f0s[n // 2]:.1f} '
            f'p75={f0s[3 * n // 4]:.1f} '
            f'max={f0s[-1]:.1f}'
        )
        f0_le30 = sum(1 for x in f0s if x <= 30)
        f0_gt30 = n - f0_le30
        print(f'F0 max<=30s: {f0_le30} ; F0 max>30s: {f0_gt30}')

    cohort_pairs = set((r['cohort_pro'], r['cohort_free']) for r in results if r.get('cohort_pro'))
    print(f'distinct cohort pairs: {len(cohort_pairs)}')

    print('\nTop 15 by VPH (full detail):')
    for r in sorted(results, key=lambda x: x.get('vph') or 0, reverse=True)[:15]:
        print(
            f'  vph={r.get("vph"):>8}  f0max={r.get("command_max"):>7.1f}  '
            f'attempt1_max={r.get("attempt_1_max"):>7.1f}  retry_max={r.get("retry_max"):>7.1f}  '
            f'retry_count={r.get("retry_count"):>4}  cohort={r.get("cohort_pro")}/{r.get("cohort_free")}  '
            f'{r["name"]}'
        )

    print(f'\nwrote {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())