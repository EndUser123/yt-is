# Staging Artifact Cleanup

The multi-account runners have one authoritative output root plus one legacy
compatibility root:

- `P:/packages/yt-is/.logs/multi_account_fetch/` is the package-owned root for
  new direct, supervised, and throughput runs.
- `P:/.logs/multi_account_fetch/` is retained as a legacy root for existing
  receipts and restart state. It is not the default for new runs.

Both roots may contain small receipts plus disposable SQLite snapshots. The canonical
databases remain under `P:/.data/yt-is/` and are never cleanup targets. There
was no existing rotation policy covering these experiment roots; this module
is the package-owned retention policy.

## Policy

- Experiment directories modified within the last hour are skipped completely.
- In older directories, files modified within the last hour are preserved.
- Directories between one hour and seven days old lose only staging
  `*.sqlite`, `*.sqlite-wal`, and `*.sqlite-shm` files. `.json`, `.md`, and
  `.txt` receipts remain.
- Directories older than seven days are removed recursively only when no file
  in them was modified within the last hour. This is the explicit evidence
  retention policy; use the dry run before applying it.
- `transcript-fallback-queue.sqlite` is a durable recovery queue and is
  protected. The canonical batch database and browser profile root are also
  protected by path guards.

## Commands

From `P:/packages/yt-is`:

```powershell
python -m csf.cleanup_staging --dry-run
python -m csf.cleanup_staging --max-age-days 7
```

With no `--root`, the CLI scans both roots. `--root PATH` bounds the sweep to
one root, and the option may be repeated for an explicit set of roots. The
CLI emits a JSON report with per-root status, planned/deleted files, byte
counts, and errors. A dry run performs no writes.

## Lifecycle integration

The multi-account CLI and unattended supervisor run a parent-tree sweep after
their work. The throughput-pair executor also sweeps after all arm receipts
and staging-integrity checks have completed. The one-hour guard intentionally
allows a just-finished child or parent validator to retain its staging DB for
the grace period; a later invocation or scheduled maintenance sweep removes
it. Cleanup errors are reported but do not change the fetch result.

This ordering matters because `run_multi_account_fetch.py` is a child of the
throughput-pair executor, which reads the mutable staging databases after the
child exits. Deleting them in the child would invalidate its own reconciliation.
