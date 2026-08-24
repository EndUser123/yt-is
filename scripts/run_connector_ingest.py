"""Thin wrapper: ingest connector batches (reddit/hn/discord) into the EF.

Separate script so run_all_syncs can invoke it as one step with its own
timeout; the real logic lives in ef.ingest_connectors."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    result = subprocess.run(
        [sys.executable, "-m", "ef.ingest_connectors"],
        cwd=str(REPO),
    )
    sys.exit(result.returncode)
