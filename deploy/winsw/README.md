# WinSW service definitions (durable copies)

The live WinSW install lives at `P:\.data\winsw\` (excluded from the
workspace repo by the `.data/*` blanket ignore — that is why these durable
copies exist here). Contents:

- `ef_warm_query.xml` — warm-model evidence query service (:6391).
- `ef_qdrant.xml` — Qdrant vector DB (:6390, gRPC :6392).

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
