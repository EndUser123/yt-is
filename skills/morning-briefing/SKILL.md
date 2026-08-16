---
name: morning-briefing
description: Run the yt-is morning briefing — one command that answers every operational question (health, backlog, taxonomy, anomalies, changes)
version: 1.0.0
enpretation: strict
triggers:
  - User asks for a status report, morning briefing, or pipeline health
  - User asks "what happened overnight" or "how are we doing"
  - User asks about transcript counts, backlog status, or anomalies
  - Session start on a new day (suggest running this first)
workflow_steps:
  - Run the deterministic script (scripts/morning_briefing.py)
  - Read the output
  - Interpret anomalies and suggest actions
  - Report to the operator
allowed_first_tools:
  - Bash
required_first_command_patterns:
  - '^python (P:/packages/yt-is/)?scripts/morning_briefing\\.py'
required_first_command_hint: Use `python scripts/morning_briefing.py` from P:/packages/yt-is for the full report.
aliases:
  - briefing
  - status report
  - pipeline health
  - morning report
depends_on_skills: [channel-intake]
---

# /morning-briefing — one command that answers every operational question

## Usage

```
python scripts/morning_briefing.py                # full text briefing
python scripts/morning_briefing.py --json         # machine-readable
python scripts/morning-briefing.py --section health  # one section only
```

## What it reports

| Section | Answers |
|---|---|
| **HEALTH** | Is the supervisor running? Any alerts? Transcripts cached? DB integrity? |
| **BACKLOG** | Pending/complete/failed counts, success rate, top failures, channel counts |
| **TAXONOMY** | Category distribution, Other/unclassified, provenance split, dead channels |
| **ANOMALIES** | Suspect transcripts, orphans, active alerts |
| **CHANGES** | Recent commits, new failures in 24h |

## Interpretation guide (for the agent reading the output)

- **Success rate < 80%** → investigate the failing chunks (check supervisor state, account receipts)
- **Suspect transcripts > 50** → consider a quality pass on short transcripts
- **Orphans > 10** → transcripts claimed complete but not on disk; investigate the fetch path
- **Active alert present** → read P:/.data/yt-is/pipeline-alert.txt and act on it
- **New failures > 1000/day** → check if a specific failure class is exploding
- **Unclassified > 20** → run `categorize --retry-other --workers 3`

## Scheduling

To get this every morning automatically:
```
schtasks /create /tn "YtisMorningBriefing" /tr "python P:\packages\yt-is\scripts\morning_briefing.py > P:\.data\yt-is\morning-briefing.txt" /sc daily /st 07:00
```

Or the operator can just say "morning briefing" to any session.
