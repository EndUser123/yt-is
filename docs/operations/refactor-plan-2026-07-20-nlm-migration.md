# PLAN: Migrate yt-is NLM surface from nlm CLI to notebooklm-py

**Target:** P:/packages/yt-is
**Worktree:** P:/.worktrees/ytis-refactor-nlm-migration-20260720-114644
**Branch:** refactor/nlm-migration-20260720-114644
**Plan head:** 6e384f9531a2f126f13e85fbc1861983edc14932
**Created:** 2026-07-20

---

## Goal

Replace every nlm CLI shell-out in yt-is with the `notebooklm-py` Python library so the pipeline gets typed `Source` objects, eliminates stdout parsing, eliminates the N-1 silent-drop bug class (github jacob-bd/notebooklm-mcp-cli#196), and consolidates auth into a single per-account cookie store.

## Decision context (recorded per user direction)

- **Profile-name mapping preserved for now** (revisit in follow-up). Workers still use `ytis-pro-worker-01..05`, `ytis-free1-worker-01..05`, `ytis-free2-worker-01..04` as routing labels. Each resolves to one of 3 Google accounts.
- **Working notebooks keep worker-name-based titles** (per user clarification). Existing `_DEFAULT_OWNER_NOTEBOOK_TITLE='yt-is-worker-01'` and per-worker titles stay.
- **Transition effort doesn't matter.** Optimize for reliability, lower maintenance, lower future cost/risk. Do the full migration, not a patch.
- **Trial 1 failed** (`--json` output has same N-1 bug). **Trial 2 succeeded** (`notebooklm-py` returns all sources including the one the CLI drops).

## Authority documents

- `docs/operations/nlm-surface-discovery-2026-07-20.md` — call-site inventory
- `docs/operations/root-cause-program.md` — C1-C5 trust floor constraints
- Trial 1/2 results — inline in chat session

## Constraints (invariants to preserve)

From `root-cause-program.md:27` and `AGENTS.md`:
1. **Path monopoly** — one write path per concern. Migration target state: CLI removed entirely (true monopoly).
2. **C1-C5 trust floor** — must remain closed. Migration must not regress: work outcomes, identity/cache write gate, durable row-merge, fail-closed auth, control-plane collapse.
3. **Never invent session/run identities** — auth migration must preserve real provenance.
4. **Multi-worker correctness** — workers operate on separate notebooks with separate credentials; the migration must preserve per-account isolation.
5. **Inline invariant A2** (`csf/nlm_batch.py:3167`): "uncorroborated list-order pairing is never used to fill gaps." Migration removes the need for this entirely (typed Source objects) but the invariant is preserved by construction, not by relaxation.

## Architecture

### Account model

3 Google accounts → 3 `storage_state.json` files:
- `~/.notebooklm/profiles/ytis-pro-account/storage_state.json` (`a.hominidae@gmail.com`)
- `~/.notebooklm/profiles/ytis-free1-account/storage_state.json` (`troup.hominidae@gmail.com`)
- `~/.notebooklm/profiles/ytis-free2-account/storage_state.json` (`brsthomson@hotmail.com`)

### Profile → account mapping

Preserve existing profile names as routing labels. New mapping table in `csf/nlm_client.py`:

```python
PROFILE_TO_ACCOUNT = {
    "ytis-pro-worker-01": "ytis-pro-account",
    "ytis-pro-worker-02": "ytis-pro-account",
    # ... etc
    "ytis-free1-worker-01": "ytis-free1-account",
    # ... etc
}
```

Revisit later: collapse profile names to direct account assignment.

### Client lifecycle

- One `NotebookLMClient.from_storage(profile=<account>)` per worker process
- Reused across batches within the worker's lifetime
- Sync wrapper: `asyncio.run()` per call site (standard pattern)
- Connection pooling: client's `keepalive=60.0` (library default)

### Auth bootstrap

Replaces `bin/csf-source:485-611` `_ensure_nlm_auth`:
- Resolve active profile → resolve account → resolve storage file path
- If storage file missing or `client.is_connected()` is False → run `python -m notebooklm login -p <account> --browser chrome` once
- After success, set `YTIS_NLM_AUTH_NONINTERACTIVE=1` for workers (same pattern as before)
- Workers load their own client via `from_storage(profile=<account>)`

## Seams (phased execution)

### Phase 1 — Add `csf/nlm_client.py` (scaffolding, no behavior change)
- New module: thin async-aware client lifecycle wrapper
- Profile→account mapping
- Sync adapter (`asyncio.run` wrapper)
- No callers yet; tests for the wrapper only
- **Verify:** unit tests for wrapper pass; existing test suite unchanged

### Phase 2 — Migrate `_add_sources_chunk` to notebooklm-py (THE BUG FIX)
- Replace CLI batch add with N `client.sources.add_url()` calls
- Replace `_extract_source_ids_from_add_stdout` with typed `Source.id` list
- Remove parse-mismatch branch (lines 2572-2586) — can't happen
- On `SourceAddError`: retry that single video once; if still failing, record failure but continue with others
- **Verify:** 5-video fetch test produces 5 transcripts (currently produces 0)
- **Dependency:** Phase 1 complete

### Phase 3 — Migrate notebook create/list/delete
- `csf/nlm_scraper.py:1491, 1766, 1778, 1796, 2566, 2592, 2619` → `client.notebooks.create/list/delete`
- `csf/nlm_content_probe.py:109` → `client.notebooks.create`
- **Verify:** existing notebook CRUD tests still pass

### Phase 4 — Migrate source list/content
- `csf/nlm_scraper.py:799, 1681` → `client.sources.list/get_fulltext`
- `csf/nlm_content_probe.py:76, 121` → `client.sources.get_fulltext/add_url`
- `csf/csf_nlm_import.py:108` → `client.sources.list`
- `csf/nlm_exporter.py:260` → `client.sources.add_text` (not URL — was text)
- **Verify:** source-list-dependent tests pass

### Phase 5 — Migrate auth bootstrap (`_ensure_nlm_auth`)
- `bin/csf-source:485-611` → uses new `csf/nlm_client.py` to validate/refresh storage file
- Removes CDP-family-refresh code path (the 0.0s no-op FM-4 from red-team)
- Removes `refresh_source_profile` / `sync_worker_profiles` calls from bootstrap
- **Verify:** bootstrap test suite; manual: fetch runs without browser popup when storage file is valid

### Phase 6 — Remove dead CLI infrastructure
- Delete `csf/nlm_auth_guard.py` entirely (with `run_nlm`)
- Delete CDP family machinery from `csf/nlm_worker_auth.py` (`AuthFamily`, `DEFAULT_FAMILIES`, `refresh_source_profile`, `sync_worker_profiles`, CDP browser roots/ports)
- Delete `_extract_source_ids_from_add_stdout`
- Delete `_ensure_nlm_auth` legacy code in `bin/csf-source` (replaced in Phase 5)
- **Verify:** grep shows zero remaining `nlm_auth_guard.run_nlm` calls; `import nlm_auth_guard` is a no-op or removed
- **Dependency:** Phases 2-5 complete

### Phase 7 — Update tests
- Rewrite `tests/test_nlm_auth_guard.py` → tests for `csf/nlm_client.py`
- Update `tests/test_nlm_batch.py` → mock `client.sources.add_url` instead of `_run_nlm`
- Update `tests/test_nlm_exporter.py` → mock client methods
- **Verify:** full test suite passes (or documents exactly which failures are pre-existing, per today's baseline)

## Stop conditions

- Phase 2 verify fails → STOP, report. The bug fix isn't working; don't proceed to Phase 3.
- Phase 5 verify fails (browser popup) → STOP, report. Auth bootstrap regression.
- Any phase introduces a C1-C5 regression → STOP, report. Trust floor is inviolable.

## Out of scope (deliberately deferred)

- Profile-name simplification (collapse `ytis-pro-worker-01..05` → just `pro`)
- Migrating the per-profile NLM-mcp-cli browser roots under `P:/.data/yt-is/browser/` (no longer needed after migration but leave for now)
- nlm CLI removal from PATH / requirements (still useful as a debugging tool)
