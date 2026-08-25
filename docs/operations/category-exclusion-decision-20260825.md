# Category exclusion decision: Health / Markets / Finance — KEEP (recommendation)

agent: zcode, 2026-08-25 (re-run of the 2026-08-24 evening analysis on live
data; all queries mode=ro against `P:/.data/yt-is/batch_status.sqlite`).
Operator word pending. This document is the evidence packet for that word.

## Question

Should Health, Markets, and Finance be added to `excluded_categories` in
`config/discovery-settings.json` (removing ~202K pending rows from the
drain and blocking ~565 channels at scan and fetch ends), per HANDOFF open
item 1 ("highest-leverage open call")?

## Live numbers (2026-08-25 ~05:30Z)

Pending by category (YouTube rows, analysis_status × channel_metadata):

| category | pending | complete | failed |
|---|---|---|---|
| AI/ML | 92,482 | 53,225 | 702 |
| Health | 76,708 | 72,765 | 957 |
| Markets | 74,240 | 24,507 | 193 |
| Finance | 51,075 | 11,385 | 66 |
| Software Engineering | 41,368 | 23,659 | 355 |
| Technology | 32,057 | 19,500 | 159 |
| Business | 25,140 | 15,446 | 502 |
| Education | 16,169 | 7,676 | 43 |
| AI News | 11,290 | 10,155 | 112 |

- H+M+F = 202,023 pending = 46% of the 435.6K pending — AND 108,657 of
  262,929 complete = 41% of the completed corpus. **Health is the #1
  completed category** (72.8K, ahead of AI/ML's 53.2K).
- Fetch frontier, last 48h YouTube completions: SWE 2,019 + Tech 1,436 +
  AI/ML 839 = 89%; Health 144 (3%); **Markets 0, Finance 0**. The drain
  frontier is already tech-weighted ~30:1 with no exclusion in place.
- Already-excluded categories (News, Politics, Music, Lifestyle, Gaming,
  Science, Physics, Entertainment, Sports) show zero pending rows — the
  6 AM enforcer works; whatever is decided applies cleanly.
- All 435.6K pending rows now have channel_metadata rows (0 unjoined).

## Recommendation: KEEP (do not exclude)

1. Exclusion blocks future discovery for the corpus's #1 completed
   category and two of its top-ten categories — the corpus's Health depth
   (72.8K transcripts; 72.3K indexed eu per the 2026-08-24 count) is a
   load-bearing input to the interest-graph validation verticals
   (longevity, options/trading are declared operator core interests).
2. The pending mass is 99.6% `no_captions` — those rows are not being
   fetched under any category; the real decision they await is the
   deferred-audio (Whisper) policy, not category membership. Excluding
   H/M/F would move the pending counter from 435.6K to ~233.6K without
   changing what gets fetched.
3. Both original motivations for exclusion were fixed by other shipped
   work: drain-selector speed (6.5s → 1.2s via the 536K excluded migration,
   5f8faaab) and scan waste (blocked-channel scan filter, 0ddb0b48).

## Falsifier (unchanged from 2026-08-24)

A 30-day window in which H/M/F takes a large share of catalog-driven
fetches (say >25% of YouTube completions) flips this to exclude — that
would mean the tech-heavy frontier was an artifact, and H/M/F pending rows
were actively competing for fetch budget.

## Cost of being wrong

Bounded and reversible: the excluded_categories list is config; the
enforcer applies on the next 6 AM run either direction. Per-channel
exceptions already exist (`promote_excluded_categories.py`); per-channel
blocklisting of specific low-yield channels remains available if scan cost
is ever the real concern.
