"""Entry point: ``python -m scripts.ops_console [--port N] [--host H]``.

On-demand local application only — no daemon, no scheduler registration.
"""

from __future__ import annotations

import argparse
import os

from nicegui import ui


def main() -> None:
    parser = argparse.ArgumentParser(prog="scripts.ops_console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8732)
    args = parser.parse_args()

    storage_secret = "ops-console-local"
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        # The baked-in secret is only safe for loopback use; require an
        # explicit secret before binding a non-loopback interface.
        storage_secret = os.environ.get("OPS_CONSOLE_STORAGE_SECRET")
        if not storage_secret:
            parser.error(
                "refusing to bind non-loopback host with the built-in storage secret; "
                "set OPS_CONSOLE_STORAGE_SECRET to a real secret first"
            )

    import scripts.ops_console.pages  # noqa: F401 - registers all routes

    ui.run(
        title="yt-is operations console",
        host=args.host,
        port=args.port,
        reload=False,
        show=False,
        storage_secret=storage_secret,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
