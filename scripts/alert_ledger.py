"""Alert ledger: transition-based alert state for the pipeline watcher.

Replaces the rewrite-every-tick alert file as the system of record (the
file remains as a human/digest projection). Each watcher tick compares
current alert lines against open events: a new condition OPENS an event,
a persisting condition UPDATES last_seen/count (and condition text), a
vanished condition RESOLVES it. Transitions append to ``ledger.jsonl``;
the open-state snapshot ``open.json`` is atomically replaced.

Dedup normalizes digits in the condition text so count-drift inside
detail strings (backlog sizes, row counts) does not flap events open and
resolved. Event ids are stable per dedupe key.

Consumer contract (agent-facing): read ``open.json`` for current state;
tail ``ledger.jsonl`` for transitions. ``allowed_actions`` is seeded
empty — the action policy is an operator decision and the loop only
recommends until that policy exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

_DIGITS = re.compile(r"\d+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dedupe_key(line: str) -> str:
    """Stable identity for an alert line, insensitive to embedded numbers."""
    return _DIGITS.sub("N", line.strip())[:160]


def severity_of(line: str) -> str:
    tag = line.split("]", 1)[0].lstrip("[").strip()
    return "warning" if tag.endswith("-warning") else "alert"


def _event_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_open(ledger_dir: Path) -> dict[str, dict]:
    snapshot = ledger_dir / "open.json"
    if not snapshot.exists():
        return {}
    try:
        return json.loads(snapshot.read_text(encoding="utf-8")).get("events", {})
    except (json.JSONDecodeError, OSError):
        # Torn/unreadable snapshot: treat as empty; the ledger retains
        # history and the next tick re-opens what is still live.
        return {}


def record(lines: list[str], ledger_dir: Path, now: str | None = None) -> list[dict]:
    """Fold current alert lines into the ledger; return transitions written.

    ``lines`` empty means the system is healthy — every open event
    resolves.
    """
    now = now or _now_iso()
    ledger_dir.mkdir(parents=True, exist_ok=True)
    open_events = load_open(ledger_dir)
    current: dict[str, dict] = {}
    transitions: list[dict] = []

    for line in lines:
        key = dedupe_key(line)
        prior = open_events.get(key)
        if prior is None:
            event = {
                "event_id": _event_id(key),
                "dedupe_key": key,
                "source_tag": line.split("]", 1)[0].lstrip("[").strip(),
                "condition": line.strip(),
                "severity": severity_of(line),
                "status": "open",
                "first_seen": now,
                "last_seen": now,
                "count": 1,
                "evidence": "pipeline_health_watch tick",
                "allowed_actions": [],
            }
            current[key] = event
            transitions.append({"event": "opened", "at": now, **_brief(event)})
        else:
            prior["last_seen"] = now
            prior["count"] = prior.get("count", 1) + 1
            prior["condition"] = line.strip()
            prior["status"] = "open"
            current[key] = prior

    for key, prior in open_events.items():
        if key not in current:
            prior["status"] = "resolved"
            prior["resolved_at"] = now
            transitions.append({"event": "resolved", "at": now, **_brief(prior)})

    _atomic_write(
        ledger_dir / "open.json",
        json.dumps({"updated": now, "events": current}, indent=1),
    )
    if transitions:
        with open(ledger_dir / "ledger.jsonl", "a", encoding="utf-8") as fh:
            for t in transitions:
                fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    return transitions


def _brief(event: dict) -> dict:
    return {
        "event_id": event["event_id"],
        "dedupe_key": event["dedupe_key"],
        "condition": event["condition"],
        "severity": event.get("severity"),
        "first_seen": event.get("first_seen"),
        "count": event.get("count"),
    }
