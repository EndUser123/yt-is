# WinSW service definitions (durable copies)

The live WinSW install lives at `P:\.data\winsw\` (excluded from the
workspace repo by the `.data/*` blanket ignore — that is why these durable
copies exist here). Contents:

- `ef_warm_query.xml` — warm-model evidence query service (:6391, plus the
  merged search_ef MCP face on :8324 since 2026-08-22).
- `ef_qdrant.xml` — Qdrant vector DB (:6390, gRPC :6392).
- `retired/search_ef.xml` — the separate search_ef service, retired
  2026-08-22 when its MCP face merged into ef_warm_query
  (`ef/warm_query_service.py:1816`; ~2.4-3.4 GB saved). Kept as archive
  only; never reinstall — the service is uninstalled from the SCM and its
  port is owned by ef_warm_query.

The full six-service drift picture (including the search-fleet mirrors in
`packages/search-research` and dotgrok `deploy/winsw/`) is declared in
`P:/.agents/config/winsw_drift_manifest.json` and checked by
`P:/.agents/scripts/check_winsw_drift.py` (daily 05:45 task
`ZcodeWinswDriftCheck`, status at `P:/.data/winsw-drift/status.json`).

Reinstalling a service from these definitions:

1. Copy the XML next to the matching WinSW `.exe` (WinSW release binary)
   under `P:\.data\winsw\` and run `<exe> install <xml>`.
2. A WinSW reinstall WIPES the delegated service DACL grant documented in
   AGENTS.md ("Agent service restarts"). Re-apply it by running
   `scripts/ytws-warm-fix.ps1` from an admin terminal (idempotent; also
   kills a squatted/broken warm-service python and verifies real query
   rows before writing its result marker).

If the live XML under `P:\.data\winsw\` is edited, copy it back here in the
same change so these copies do not drift.
