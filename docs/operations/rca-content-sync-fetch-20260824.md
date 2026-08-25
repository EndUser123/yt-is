# RCA: YtisContentSync nightly exit 1 (fetch stage)

agent: zcode (read-only investigator), 2026-08-24. No files edited in the repo; no git commands run.

## 1. Task definition

`MSYS_NO_PATHCONV=1 schtasks /query /tn YtisContentSync /xml` succeeded. Action:

```
Command: powershell.exe
Arguments: -NoProfile -WindowStyle Hidden -Command "python P:\packages\yt-is\scripts\run_all_syncs.py *>> P:\packages\yt-is\.logs\all_syncs\$(Get-Date -Format yyyyMM).log"
WorkingDirectory: P:\packages\yt-is
Trigger: daily 06:00 (America/Chicago, -06:00 offset)
```

Entry script: `P:\packages\yt-is\scripts\run_all_syncs.py`. Monthly log: `P:\packages\yt-is\.logs\all_syncs\202608.log` (mixed UTF-8/UTF-16 from PowerShell `>>`; the UTF-16 tail decodes only after stripping NULs).

## 2. Exact failure signature (latest run, 2026-08-24)

From `P:\packages\yt-is\.logs\all_syncs\202608.log` (line numbers in the 2026-08-24 06:00:03 local run, `Started: 2026-08-24 06:00:03` at file line 148104; timestamps in the child logs are UTC, 06:00 local = 12:00Z):

```
[pipeline] sync complete in 1515.4s
[pipeline] Phase 2: VERIFY — checking pending backlog...
[pipeline] verify OK — pending: 972,221 (delta: +2,641)
[pipeline] warning: 536125 pending rows on blocked channels (coordinator will skip)
[pipeline] Phase 3: FETCH — launching supervisor (EXECUTE)...
[pipeline] campaign output root: unattended-20260824T123342Z
[pipeline] campaign state: supervisor_state.json
[pipeline] FETCH supervisor exited 1
[pipeline] receipt: P:\packages\yt-is\.logs\intake_pipeline\20260824T120010Z\pipeline_receipt.json
```
(final line before the pipeline's digest output; run summary ends with `Some syncs failed` and `✗ YouTube Channel Sync` in the per-sync marks.)

Receipt `P:\packages\yt-is\.logs\intake_pipeline\20260824T120010Z\pipeline_receipt.json` (created 2026-08-24T12:00:16Z):

```json
"fetch": {"elapsed_s": 312.3, "returncode": 1, "supervisor_status": "partial"},
"status": "fetch_failed",
"sync": {"channels_processed": 2865, "new_videos_found": 35063, "returncode": 0}
```

Supervisor state `...\20260824T120010Z\supervisor_state.json`, last chunk:

```json
"returncode": 1,
"status": "partial",
"selected_count": 400,
"selected_complete_count": 347,
"selected_status_counts": {"complete": 347, "failed": 19, "pending": 34},
"failure_reason": null, "failure_stage": null, "failure_type": null,
"staging_cleanup": {"status": "completed", "files_deleted": 0, "errors": []}
```

DB ground truth (`P:\.data\yt-is\batch_status.sqlite`, read-only) for that chunk's 400 selected IDs, by account:

| account | complete | failed | pending | pending rows' updated_at |
|---|---|---|---|---|
| brsthomson | 127 (all 2026-08-24) | 6 | 0 | — |
| troup.hominidae | 123 (all 2026-08-24) | 10 | 0 | — |
| a.hominidae | 97 (2026-08-24, last 12:38:43Z) | 3 | **34** | **all frozen at 2026-08-20T06:11:17Z — never touched by this run** |

Failed rows this run (updated 12:37:54–12:38:43Z), `failure_reason` values verbatim from the DB:
- `Source add failed` (14)
- `Source add failed; materialization terminal error: SourceAddError (cause=RPCError, rpc_code=9)` (4)
- `Fetch failed for 9be3383e-df4d-460e-9e45-a4c4a8f2242c: nlm_content_below_threshold` (1)

Chronicity — same terminal signature every night (from each night's `supervisor_state.json`, last chunk):

| run | chunks OK before death | last chunk counts | elapsed |
|---|---|---|---|
| 20260821T120002Z | 36 | 343 complete, 9 failed, 48 pending, rc 1 | 15920s |
| 20260822T120005Z | 9 | 311 complete, 40 failed, 49 pending, rc 1 | 3533s |
| 20260823T120036Z | 12 | 113 complete, 7 failed, 280 pending, rc 1 | 3890s |
| 20260824T120010Z | 0 | 347 complete, 19 failed, 34 pending, rc 1 | 312s |

Every night: the campaign runs chunks until one account child exits non-zero mid-assignment, leaving rows `pending` at snapshot time → chunk `partial` and non-terminalized → campaign stops → task exits 1. (Prior nights' pending rows are completed later by retry runs, so they no longer show pending in today's DB; 2026-08-24's 34 a.hominidae rows still do.)

## 3. Causal chain (code trace)

1. `P:\packages\yt-is\scripts\run_all_syncs.py` runs `scripts\run_intake_pipeline.py` for the YouTube stage; any stage rc != 0 → summary mark ✗ → prints `Some syncs failed` → `sys.exit` non-zero → task result 1. Health watcher alerts on it.
2. `P:\packages\yt-is\scripts\run_intake_pipeline.py` `phase_fetch()` (line ~221) launches `scripts\run_unattended_backlog.py` (supervisor). Receipt logic line 512–517: `fetch_rc != 0` → `receipt["status"] = "fetch_failed"`. Log line `[pipeline] FETCH supervisor exited 1` at run_intake_pipeline.py (grep hit near line 428's help text; the print itself is in phase_fetch's caller).
3. Supervisor `P:\packages\yt-is\scripts\run_unattended_backlog.py` run_supervisor(): on `returncode != 0` it sets `state["status"] = "stopped"` and breaks (line ~1904-1907), so one bad chunk ends the whole nightly campaign with 49 chunks of budget unspent.
4. Coordinator `P:\packages\yt-is\scripts\run_multi_account_fetch.py` runs one `bin\csf-source fetch` child per account in parallel (line ~1234 command; per-account `run_account_with_settings`). After children exit it reads DB statuses (`read_selected_status_snapshot`) and classifies (line ~1434 `classify_outcome`): not all complete → `partial`. Exit path (line ~2388-2397): `partial` exits 0 only if every selected row is terminal (`complete`/`failed`, `_partial_payload_is_terminalized`); 2026-08-24 had 34 `pending` → `return 1`.
5. Root trigger: the `a.hominidae` child process died/exited non-zero ~12:38:43–12:38:54Z, 97/134 done, its remaining 34 rows never written (updated_at still 2026-08-20). `process_failed` was true (that account's `returncode != 0 or error` — `run_multi_account_fetch.py` line ~2018), which is also why `classify_outcome` could not return `completed`.
6. The exact child error is unrecoverable: per-account stdout/stderr/receipts/events live only inside the chunk output root (`P:\packages\yt-is\.logs\multi_account_fetch\unattended-<stamp>\chunk-0001\...`), and that tree is deleted by staging cleanup / has already vanished (the whole `.logs\multi_account_fetch\` root is now absent). The chunk record's `failure_reason` is `null` because the coordinator does not propagate per-account error text into the summary — only the boolean `process_failed`.

Not the cause: sync (rc 0, 35,063 new videos), verify (ok), RSS 404/503 feed errors (separate stages, both completed), preflight (7/8 pass, memory 93% warn only).

## 4. Fix classification: DEEPER (with two concrete mechanical enablers)

Owner stream: yt-is fetch pipeline (`csf-source fetch` child behavior under account `a.hominidae`, coordinator `run_multi_account_fetch.py`, supervisor `run_unattended_backlog.py`).

A code change cannot be authorized yet because the child's fatal error text is destroyed every night. Two mechanical edits are justified on the existing evidence:

- M1 (evidence preservation, mechanical): in `P:\packages\yt-is\scripts\run_multi_account_fetch.py`, copy each account result's `error`, `returncode`, `stderr_path` (and tail of stderr) into the summary payload next to `assignment_ownership`, so `supervisor_state.json` survives staging cleanup. Edit point: the `results` loop at line ~2015-2030 already has the data; add it to the payload before `print(json.dumps(payload...))` (line ~2381). This makes the next night self-diagnosing.
- M2 (optional, decision needed): supervisor `run_unattended_backlog.py` line ~1904 treats any single rc!=0 chunk as campaign-fatal while 34-pending interruptions are recoverable by retry. Whether to continue to the next chunk (or relaunch the failed chunk once) is a policy decision requiring M1's evidence first — do not change blind.

What the fix decision needs: one night's surviving per-account stderr from M1 showing why `a.hominidae`'s `csf-source fetch` exits non-zero ~5 minutes in (candidate hypotheses: uncaught exception in the NLM worker loop; RPCError rpc_code=9 storm escalating to a fatal path; child killed by memory pressure — preflight warned `memory at 93%`). Note `a.hominidae` is the only account on the shared canonical storage (`storage_state.json`, verified ok in `P:\packages\yt-is\.logs\term_fd123f2f.jsonl` at 2026-08-24T12:33:52Z), a structural difference from the two per-account-storage profiles that never fail this way.

## 5. Risks

- R1: M2 (continue-on-failed-chunk) without M1 evidence could mask a real systemic failure and burn quota on a poisoned chunk.
- R2: The pending-34 rows are re-selected by retry runs (proven by 8/21–8/23 rows now complete), so backlog loss is not occurring; do not "fix" by bulk-requeueing.
- R3: Secondary anomaly observed: `P:\packages\yt-is\.logs\intake_pipeline\` has empty run dirs every ~75 min from 20260824T184521Z through 20260825T033927Z (no receipt at all) — a monitor loop is starting pipelines that die before writing anything. Separate issue; do not conflate with the nightly task failure.
- R4: `.logs\multi_account_fetch\` root was deleted despite cleanup reporting `files_deleted: 0` — some other actor removes campaign artifacts; M1 must write outside that tree.

## 6. Falsifier

Run one night with M1 in place. If a night ends rc 1 with all three account children at returncode 0 (no `process_failed`), the "a.hominidae child dies mid-assignment" theory is wrong and the fault is in classify/exit logic itself. If the child error is captured and is a plain uncaught exception, the fix is mechanical in `csf-source fetch`; if it is OOM/kill, the fix is capacity configuration.

## 6a. DECOY WARNING for the M1 evidence read (operator directive, 2026-08-25)

**The 2026-08-25 06:00 run's M1 capture is a decoy — do not diagnose M2 from it.** That run died pre-work in ~17s with `ModuleNotFoundError: No module named 'dev'` across all three accounts because an unattributed sweep had deleted `dev/` from the working tree; the package was restored via `git restore` at ~13:35Z, which leaves no trace (no commit, no log entry, clean status). The artifact says "missing module," the repo says the module exists — the natural conclusion would fix an already-fixed problem and mis-decide M2. **The 2026-08-26 06:00 run is the first true chronic-signature capture.** Also: children exit rc=0 on total worker-spawn failure, so classify by the stderr tail, not the returncode.

## Accounting

- Verified: task XML, log path, latest-run errors (quoted), receipt, supervisor state, DB per-account statuses, exit-code logic in all three pipeline layers.
- Not recoverable: the 2026-08-24 a.hominidae child stderr (deleted with the chunk output root); chronicity confirmed for 8/21–8/24 only.
