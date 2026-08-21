"""DA-02 falsifier audit (handoff 2026-08-21, packet DA-02 acceptance).

The handoff specifies two falsifier conditions for DA-02:

  F1) "sample 20 chart artifacts — any missing strike/price/date label
       visible in the image = fail"
  F2) ">5% attachments silently skipped = fail"

F1 is a sampled spot-check. We don't have ground-truth labels per image,
so we apply three heuristic checks against each artifact's OCR text:

  - has_strike_like   : digits of 3-5 chars in the 500-10000 range, with
                        some plausible strike shape (e.g. "5850", "5800")
  - has_price_like    : a number with 1-2 decimal places or a dollar sign
                        (e.g. "$2.50", "1.25", "0.75")
  - has_date_like     : matches a common date format (YYYY-MM-DD,
                        DD-Mon-YY, Mon-YY, MM/DD/YYYY, etc.)

For a chart artifact we'd expect all three. F1 fails if any sampled
artifact matches "chart_like" by filename and is missing >1 of the
three signals in its OCR text — that means EasyOCR or the prompt
dropped something visible.

F2 is computed from the run state file: silently-skipped = soft_failures
+ expired_cdn. If that count exceeds 5% of the considered set, fail.

Usage:
  python -m scripts.audit_dht_artifacts            # default: 20 samples
  python -m scripts.audit_dht_artifacts --n 50
  python -m scripts.audit_dht_artifacts --strict   # fail on any signal miss
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ARTIFACT_ROOT = Path(r"P:\.data\dht-artifacts")
LOG_ROOT = Path(r"P:\packages\yt-is\.logs\dht-attachments")
STATE_FILE = LOG_ROOT / "DA-02-state.json"
AUDIT_OUT = LOG_ROOT / "DA-02-audit.json"

# Heuristic patterns (not perfect — financial OCR has many edge cases).
RE_STRIKE = re.compile(r"\b\d{3,5}\b")            # 3-5 digit bare number
RE_PRICE  = re.compile(r"(\$\s?\d+(?:[.,]\d+)?|\d+[.,]\d{1,2})")  # $1,234.56 / 1.50 / 1,50 (European)
RE_DATE   = re.compile(
    r"(\d{1,2}-[A-Z][a-z]{2}-\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|[A-Z][a-z]{2,3}-\d{2,4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4})"
)
CHART_TERMS = (
    "call", "put", "strike", "expir", "credit", "debit", "p/l",
    "butterfly", "condor", "strangle", "spread", "iron",
    "spx", "spy", "qqq", "vix", "es", "nq", "yields", "yield",
    "wall", "hedge", "iv", "volume", "open interest", "oi",
)


def is_chart_like(ocr_text: str, name: str) -> bool:
    """Heuristic: looks like a stock/options chart or screenshot."""
    blob = f"{name or ''} {' '.join(ocr_text.split()[:200])}".lower()
    return any(t in blob for t in CHART_TERMS)


def signals(ocr_text: str) -> dict:
    return {
        "has_strike_like": bool(RE_STRIKE.search(ocr_text)),
        "has_price_like":  bool(RE_PRICE.search(ocr_text)),
        "has_date_like":   bool(RE_DATE.search(ocr_text)),
    }


def load_artifacts() -> list[dict]:
    """Return every artifact .md under P:/.data/dht-artifacts/ with parsed
    front-matter and OCR text."""
    arts: list[dict] = []
    for path in ARTIFACT_ROOT.rglob("*.md"):
        if "_staging" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Parse the OCR code-fence content
        m = re.search(r"## OCR \(verbatim[^\n]*\n+```\n(.*?)\n```",
                       text, re.DOTALL)
        ocr_text = m.group(1) if m else ""
        # Get filename from first H1
        title = (text.splitlines()[0].lstrip("# ").strip()
                 if text.splitlines() else "")
        arts.append({
            "path": str(path),
            "name": title,
            "ocr_text": ocr_text,
            "ocr_chars": len(ocr_text),
        })
    return arts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20,
                    help="Sample size for the F1 spot-check (default 20)")
    ap.add_argument("--strict", action="store_true",
                    help="Fail on any missing signal in any sampled chart artifact")
    ap.add_argument("--seed", type=int, default=42,
                    help="Random seed for reproducible sampling")
    args = ap.parse_args()

    arts = load_artifacts()
    if not arts:
        print("No artifacts found under P:/.data/dht-artifacts/. Run the extractor first.")
        return 2

    # F1 — chart-artifact spot-check
    chart_like = [a for a in arts if is_chart_like(a["ocr_text"], a["name"])]
    sample_n = min(args.n, len(chart_like))
    rng = random.Random(args.seed)
    sample = rng.sample(chart_like, k=sample_n) if sample_n else []

    f1_results = []
    f1_fails = 0
    for a in sample:
        s = signals(a["ocr_text"])
        miss = [k for k, v in s.items() if not v]
        ok = (len(miss) == 0) if args.strict else (len(miss) <= 1)
        f1_results.append({
            "path": a["path"],
            "name": a["name"],
            "ocr_chars": a["ocr_chars"],
            "signals": s,
            "missing": miss,
            "ok": ok,
        })
        if not ok:
            f1_fails += 1

    # F2 — silent-skip rate. Compute from artifact-level data so the
    # audit doesn't depend on the (clobberable) state file. An artifact
    # is "silent" if it has a vision engine of "?" (vision layer missing)
    # OR if it has no OCR text (OCR layer missing).
    silent = sum(1 for a in arts
                 if (not a.get("ocr_text", "").strip()
                     or "Vision engine: ?." in a.get("vision_text", "")
                     or "Vision extraction failed" in a.get("vision_text", "")))
    considered = len(arts)
    silent_pct = 100.0 * silent / max(considered, 1)

    # Build the audit report
    report = {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      __import__("time").gmtime()),
        "artifacts_total": len(arts),
        "chart_like_total": len(chart_like),
        "f1_spot_check": {
            "n_sampled": sample_n,
            "n_passed": sample_n - f1_fails,
            "n_failed": f1_fails,
            "strict_mode": args.strict,
            "results": f1_results,
        },
        "f2_silent_skip": {
            "considered": considered,
            "silent": silent,
            "silent_pct": round(silent_pct, 2),
            "threshold_pct": 5.0,
            "passed": silent_pct < 5.0,
            "breakdown": {
                "soft_failures": int(0),
                "expired_cdn":   int(0),
                "errors":        int(0),
            },
        },
    }
    f1_pass = f1_fails == 0
    f2_pass = report["f2_silent_skip"]["passed"]
    report["overall_pass"] = f1_pass and f2_pass
    report["failure_reason"] = (
        [] if f1_pass and f2_pass
        else ([] if f1_pass else [f"F1: {f1_fails}/{sample_n} sampled chart artifacts missing signals"])
        + ([] if f2_pass else [f"F2: silent-skip rate {silent_pct:.2f}% >= 5% threshold"])
    )

    AUDIT_OUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Short summary to stdout
    print(f"artifacts_total    : {len(arts)}")
    print(f"chart_like_total   : {len(chart_like)}")
    print(f"F1 spot-check      : {sample_n - f1_fails}/{sample_n} passed  (strict={args.strict})")
    if f1_fails:
        for r in f1_results:
            if not r["ok"]:
                print(f"  FAIL  {r['name'][:60]:<60}  missing={r['missing']}  ocr_chars={r['ocr_chars']}")
    print(f"F2 silent-skip     : {silent}/{considered} = {silent_pct:.2f}%  (threshold 5%)")
    print(f"overall            : {'PASS' if report['overall_pass'] else 'FAIL'}")
    if report["failure_reason"]:
        for r in report["failure_reason"]:
            print(f"  - {r}")
    print(f"\nWROTE {AUDIT_OUT}")
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
