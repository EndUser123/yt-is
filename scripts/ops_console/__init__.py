"""yt-is operations console — read-only NiceGUI UI over ``pipeline_monitor``.

Architecture authority:
``docs/research/yt-is-console-ui-architecture-decision-20260817.md``
(NiceGUI, closed for this increment). The monitor owns every operational
semantic; this package only presents, navigates, and caches read models.
Launch with ``python -m scripts.ops_console``.
"""
