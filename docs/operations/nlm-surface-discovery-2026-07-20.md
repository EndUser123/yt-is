# Discovery: yt-is NLM surface — CLI shell-outs vs notebooklm-py migration

**Date:** 2026-07-20
**Trigger:** Source-add silently reports N-1 IDs (github issue #196), blocking the NLM fetch path.
**Question:** What is the full surface of NLM CLI dependencies in yt-is, and what would migration to `notebooklm-py` (the Python library) cover?

---

## NLM CLI invocation sites in yt-is (canonical source)

All CLI invocations funnel through one of two helpers:

- `csf/nlm_auth_guard.py:123` `run_nlm(args, timeout_s, env)` — the canonical wrapper (adds profile args, captures stdout/stderr, handles timeout)
- `csf/nlm_scraper.py:1419` `_run_nlm(args, timeout)` — local wrapper around `nlm_auth_guard.run_nlm` used only inside `NLMBatchScraper`

There are **no raw `subprocess.run(['nlm', ...])` calls** in production code (only in `test_nlm_query.py` and `tests/test_benchmark_methods.py`).

## Call-site inventory by NLM command

| Command | Sites (file:line) | Purpose | notebooklm-py equivalent |
|---|---|---|---|
| `login --check` | `csf/transcript.py:315, 1537`; `csf/sharded_lane_series.py:262` | Liveness probe | `client.is_connected()` (approximate) |
| `login --force` | `csf/transcript.py:1553`; `csf/nlm_worker_auth.py:414`; `csf/sharded_lane_series.py:309`; `csf/nlm_scraper.py:1442, 1513, 1787` | Interactive/force re-auth | `client.refresh_auth()` or `from_storage(profile=...)` reload |
| `notebook list` / `--json` | `csf/nlm_scraper.py:1778, 1796, 2566` | List notebooks, auth probe | `await client.notebooks.list()` |
| `notebook create` | `csf/nlm_scraper.py:1491`; `csf/nlm_content_probe.py:109` | Create notebook | `await client.notebooks.create(title)` |
| `notebook delete --confirm` | `csf/nlm_scraper.py:1766, 2592, 2619` | Cleanup | `await client.notebooks.delete(id)` |
| **`source add --url` / `--wait`** | `csf/nlm_scraper.py:1556`; `csf/nlm_content_probe.py:121`; `csf/csf_nlm_ingest.py:94`; `csf/nlm_batch.py:2394`; `csf/nlm_exporter.py:260` (`--text` not URL) | **The bug site.** Source add | **`await client.sources.add_url(nb_id, url, wait=True)`** — returns typed `Source` with ID directly |
| `source list --json` | `csf/nlm_scraper.py:799`; `csf/csf_nlm_import.py:108` | List sources for mapping | `await client.sources.list(nb_id)` |
| `source content --json` | `csf/nlm_scraper.py:1681`; `csf/nlm_content_probe.py:76` | Read source content (transcript) | `await client.sources.get_fulltext(source_id)` |
| (query, scrape, etc.) | n/a | yt-is does not use nlm-cli for chat/query | n/a |

## Auth-state coupling (critical)

| yt-is layer | What it does | notebooklm-py equivalent |
|---|---|---|
| `~/.notebooklm-mcp-cli/profiles/<name>/{cookies,metadata}.json` | The on-disk cookie store nlm CLI reads | `from_storage(profile=...)` reads the same store |
| `csf/nlm_worker_auth.py` family machinery (`refresh_source_profile`, `sync_worker_profiles`) | Propagates one source profile's cookies to sibling profiles via filesystem copy | **None — notebooklm-py has no family concept.** Each `from_storage(profile=X)` loads that profile independently. |
| `_ensure_nlm_auth` in `bin/csf-source:485` | Bootstrap: interactive login, then sets `YTIS_NLM_AUTH_NONINTERACTIVE=1` | Per-profile `from_storage` would replace this entirely |

## Tests covering this surface

- `tests/test_nlm_auth_guard.py` — tests `run_nlm` directly (3 sites: lines 490, 508, 526)
- `tests/test_nlm_batch.py:7283` — mocks `_run_nlm` for the batch path
- `tests/test_nlm_exporter.py:248, 297, 337` — mocks `nlm_auth_guard.run_nlm` for exporter
- All these tests would need rewriting if the underlying call layer changes.

---

## Constraint audit (per source-authority-discovery skill)

| citing_artifact | constraint (verbatim or paraphrased) | conflict_class | resolution_options |
|---|---|---|---|
| `docs/operations/root-cause-program.md:27` | "Prefer path monopoly (one write path per concern) over dual paths" | **stress** — adding notebooklm-py creates a second NLM access path alongside the CLI | (a) Full migration: remove CLI shell-outs entirely (true monopoly); (b) Targeted migration: replace only source-add, leave others as CLI; (c) No migration, work around the CLI bug via `--json` output |
| `AGENTS.md:44` | "Prefer path monopoly; do not leave serial vs shared outcome algebras diverging" | **stress** — same as above | same |
| `csf/nlm_batch.py:3167` (inline invariant A2) | "uncorroborated list-order pairing is never used to fill gaps" | **none** — notebooklm-py returns typed Source objects, no order pairing needed | n/a |
| `csf/nlm_worker_auth.py:39-62` (`DEFAULT_FAMILIES`) | 3-family, 3-account auth structure | **stress** — notebooklm-py has no family concept; migration loses family-based cookie propagation | (a) Loop `from_storage(profile=p)` per profile individually; (b) Build a new propagation layer on top of `notebooklm.cookie_persistence` |
| `CODEX_MEMORY.md:9` | "no_captions is not the same thing as audio_only; treat it as a routing hint" | **none** — unrelated | n/a |

---

## Three trials — what each would prove

### Trial 1: `--json` output from `nlm source add` (smallest change)

**Question:** does `nlm source add ... --json` return accurate source IDs even for the silent Nth source?

**Test:**
```bash
nlm source add <nb_id> --url <u1> --url <u2> --url <u3> --url <u4> --url <u5> --wait --json --profile ytis-pro-worker-01
```

**Decision criterion:** if JSON output contains 5 source IDs even when stdout text shows 4, we can switch `_extract_source_ids_from_add_stdout` to parse JSON instead. ~10-line change to `_add_sources_chunk`. No library migration.

**Falsifier:** if JSON output also omits the 5th source ID (same status-3 bug), this trial fails and we move to Trial 2.

**Effort:** 5 minutes.

### Trial 2: `notebooklm-py` for source-add only (targeted migration)

**Question:** does `await client.sources.add_url(nb_id, url, wait=True)` return a `Source` with a valid ID for every video, including the one the CLI silently skips?

**Test:** write a small async script that:
1. Creates a notebook via `client.notebooks.create()`
2. Adds the same 5 videos via `client.sources.add_url()` in a loop
3. Prints each returned `Source.id` and `.status`
4. Lists sources via `client.sources.list()` to confirm count

**Decision criterion:** if all 5 return a `Source` object with an ID, migrate `_add_sources_chunk` to use notebooklm-py for the source-add step only. Everything else (login, notebook create/delete, source list) stays on the CLI.

**Scope of change:** 1 function (`_add_sources_chunk`), ~50 lines, plus async adapter (yt-is is sync). Touches `_last_added_source_ids` semantics (becomes the list returned from the typed Source objects).

**Falsifier:** if notebooklm-py returns the same status-3 silent behavior (the library may share the bug with the CLI since both hit the same Google RPC).

**Effort:** 30–60 minutes to write and run the trial; half-day to migrate if successful.

### Trial 3: full migration to notebooklm-py

**Question:** is notebooklm-py a viable replacement for all 6 NLM CLI operations yt-is uses?

**Test:** out of scope for today. Would require migrating ~20 call sites, rewriting auth bootstrap, replacing the family propagation layer, and rewriting 4 test files.

**Decision criterion:** only pursue if Trials 1 and 2 both fail.

**Effort:** 2–3 days of focused work. Not recommended today.

---

## Recommendation

**Run Trial 1 first.** It's 5 minutes, it tests the maintainer's own recommended workaround (`--json` output), and if it works the fix is a ~10-line change to parse JSON instead of stdout text. No library migration, no async/sync bridge, no path-monopoly stress.

**Run Trial 2 only if Trial 1 fails.** Trial 2 is the targeted migration — it uses notebooklm-py for the one operation where the CLI is broken, and leaves everything else alone. This is a real change but bounded in scope.

**Trial 3 is not on the table today.** It's the right answer if notebooklm-py proves reliable, but it's a multi-day project and we don't have evidence yet that the library is production-ready for yt-is's scale.

---

## Unknowns I'm not resolving in this discovery

- Whether `nlm source add --json` actually contains the missing 5th source ID (only Trial 1 can show this)
- Whether notebooklm-py hits the same status-3 race (only Trial 2 can show this)
- Whether the existing test suite (`tests/test_nlm_*.py`) mocks the CLI at the right layer to survive a partial migration — this needs a separate look if Trial 2 proceeds
- Whether the `csf/nlm_worker_auth.py` family machinery has value beyond cookie propagation (e.g., account-tier isolation, quota distribution) that would be lost if auth moves per-profile into notebooklm-py

These are all answerable by Trials 1 and 2 — not by more discovery.
