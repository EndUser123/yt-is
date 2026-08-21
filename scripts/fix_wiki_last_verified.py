#!/usr/bin/env python
"""Add last_verified to wiki pages missing it, using `created` or
`updated` as the initial value (conservative: assume verified at
creation unless updated date exists). Reports pages with neither.

Usage: python fix_wiki_last_verified.py [--dry-run]
"""

from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

VAULT = Path(r"P:/.data/wiki/concepts")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    fixed, no_dates, already = 0, [], 0
    for p in sorted(VAULT.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        if re.search(r"^last_verified:\s*\S", text, re.M):
            already += 1
            continue
        # use `updated` if present, else `created`, else skip
        m = re.search(r"^updated:\s*(\S+)", text, re.M)
        if not m:
            m = re.search(r"^created:\s*(\S+)", text, re.M)
        if not m:
            no_dates.append(p.name)
            continue
        date = m.group(1)
        # insert after the `created:` line (or `updated:` if no created)
        created_m = re.search(r"^created:\s*\S+", text, re.M)
        if created_m:
            insert_after = created_m.end()
        else:
            insert_after = m.end()
        new_text = text[:insert_after] + f"\nlast_verified: {date}" + text[insert_after:]
        if not a.dry_run:
            p.write_text(new_text, encoding="utf-8")
        fixed += 1

    print(f"already has last_verified: {already}")
    print(f"{'would fix' if a.dry_run else 'fixed'} (from updated/created): {fixed}")
    print(f"no date fields at all (need manual): {len(no_dates)}")
    if no_dates:
        for f in no_dates[:10]:
            print(f"  {f}")
        if len(no_dates) > 10:
            print(f"  ... and {len(no_dates)-10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
