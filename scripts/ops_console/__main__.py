"""Entry point: ``python -m scripts.ops_console [--port N] [--host H]``.

On-demand local application only — no daemon, no scheduler registration.
"""

from __future__ import annotations

import argparse

from nicegui import ui


def main() -> None:
    parser = argparse.ArgumentParser(prog="scripts.ops_console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8732)
    args = parser.parse_args()

    import scripts.ops_console.pages  # noqa: F401 - registers all routes

    ui.run(
        title="yt-is operations console",
        host=args.host,
        port=args.port,
        reload=False,
        show=False,
        storage_secret="ops-console-local",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
