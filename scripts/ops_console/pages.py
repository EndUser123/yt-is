"""NiceGUI pages for the ops console (routing + presentation only).

Every semantic value displayed comes from ``scripts.pipeline_monitor`` via
``viewmodels``; this module owns layout, navigation, and URL state only.
The console is read-only everywhere and contains no control actions.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from nicegui import background_tasks, context, ui

from scripts.ops_console import viewmodels as vm
from scripts.ops_console.backend import get_backend

RESPONSE_TIMEOUT_S = 30.0  # slow monitor calls exceed the 3 s NiceGUI default


def _client_alive(client) -> bool:
    """Liveness guard: never push updates into a departed browser client.

    ``context.client`` does not resolve inside background tasks, so pages
    capture their client at build time and pass it here.
    """
    try:
        return bool(client) and not client.is_deleted
    except Exception:  # noqa: BLE001 - any failure means "not alive"
        return False


@contextmanager
def shell(title: str):
    ui.query("body").classes("bg-slate-950 text-slate-100")
    with ui.header().classes("bg-slate-900 items-center gap-4"):
        ui.button(on_click=lambda: ui.navigate.to("/operations"), icon="home").props("flat dense color=grey-6")
        ui.label("yt-is operations console").classes("font-bold text-sm")
        ui.space()
        ui.link("Operations", "/operations").classes("text-sky-300 no-underline")
        ui.link("Chunks", "/operations/chunks").classes("text-sky-300 no-underline")
    with ui.column().classes("w-full max-w-[1400px] mx-auto p-4 gap-4"):
        ui.label(title).classes("text-2xl font-bold")
        yield


def _raw_json_drawer(payload: dict, label: str):
    """Raw monitor output as collapsible fallback evidence, never primary UX."""
    with ui.expansion(label).classes("w-full"):
        ui.code(json.dumps(payload, indent=2, default=str)[:100000], language="json").classes(
            "max-h-96 overflow-auto text-xs"
        )


def _not_found(view: dict):
    with ui.card().classes("bg-slate-900 w-full"):
        ui.icon("search_off", color="amber").classes("text-2xl")
        ui.label(
            f'{view.get("kind")} not found: {view.get("identifier")}'
        ).classes("font-mono")
        ui.label(
            "The monitor has no record for this identifier. Check the id or follow a link from the console."
        ).classes("text-slate-400 text-sm")


# ---------------------------------------------------------------- health ----

@ui.page("/operations", response_timeout=RESPONSE_TIMEOUT_S)
async def health_page():
    with shell("Operational health"):
        client = context.client
        box = ui.column().classes("gap-2 w-full")
        shown: dict = {"report": None}

        def render(view: dict, refreshing: bool = False):
            box.clear()
            with box:
                if refreshing and shown["report"] is not None:
                    with ui.row().classes("items-center gap-2"):
                        ui.spinner("dots", size="sm")
                        ui.label("refreshing health — previous result shown below").classes(
                            "text-slate-400 text-xs"
                        )
                error = view.get("error")
                if error and shown["report"] is None:
                    ui.label(f"health unavailable: {error}").classes("text-red-400 text-lg")
                    return
                if error and shown["report"] is not None:
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("error", color="red").classes("text-sm")
                        ui.label(f"last refresh failed ({error}); previous result shown").classes(
                            "text-red-300 text-xs"
                        )
                state = view.get("state", "?")
                tone = "text-emerald-400" if "HEALTHY" in state or state in {"COMPLETED", "PLANNED"} else (
                    "text-amber-300" if "PAUSED" in state else "text-red-400"
                )
                ui.label(state).classes(f"text-3xl font-mono font-bold {tone}")
                if view.get("explanation"):
                    ui.label(view["explanation"]).classes("text-slate-200 text-lg")
                if view.get("state_reason"):
                    ui.label(f'state reason: {view["state_reason"]}').classes("text-slate-400 text-xs font-mono")
                for alert in view.get("alerts", []):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("warning", color="amber").classes("text-sm")
                        ui.label(f'{alert.get("code")}: {alert.get("detail")}').classes(
                            "font-mono text-xs"
                        )
                with ui.card().classes("bg-slate-900 w-full"):
                    ui.label("Why — resume mechanism chain (monitor output)").classes("font-bold")
                    for item in view.get("chain", []):
                        with ui.row().classes("gap-2"):
                            value = item.get("value")
                            ui.label(str(value)).classes(
                                "font-mono text-xs "
                                + ("text-red-300" if value is False else "text-slate-300")
                            )
                            ui.label(item.get("label", "")).classes("text-slate-400 text-xs")
                backlog = view.get("backlog") or {}
                with ui.row().classes("gap-8"):
                    for key in ("pending", "complete", "failed"):
                        with ui.column().classes("gap-0"):
                            ui.label(str(backlog.get(key, "?"))).classes("text-xl font-mono")
                            ui.label(key).classes("text-slate-400 text-xs")
                freshness = view.get("freshness") or {}
                if freshness:
                    with ui.card().classes("bg-slate-900 w-full"):
                        ui.label("Evidence freshness (monitor verdicts)").classes("font-bold text-sm")
                        with ui.row().classes("gap-2 flex-wrap"):
                            for source, verdict in sorted(freshness.items()):
                                color = {
                                    "fresh": "green",
                                    "stale": "orange",
                                    "missing": "red",
                                    "historical": "grey",
                                }.get(verdict, "grey")
                                ui.badge(f"{source}: {verdict}", color=color)
                last = view.get("last_chunk")
                if last:
                    with ui.card().classes("bg-slate-900 w-full"):
                        ui.label(
                            f'last chunk {last.get("chunk")} · status {last.get("status")} · '
                            f'completion {last.get("completion_rate")} · degraded={last.get("degraded")} · '
                            f'rpc9 {last.get("rpc9_add_errors")}'
                        ).classes("text-sm font-mono")
                accounting = view.get("work_accounting")
                if accounting:
                    with ui.expansion("Work accounting (acquisitions vs reconciliations — monitor output)").classes("w-full"):
                        ui.code(json.dumps(accounting, indent=2, default=str), language="json").classes("text-xs")
                if shown["report"] is not None:
                    _raw_json_drawer(shown["report"], "Raw health JSON (fallback evidence)")

        async def load():
            if shown["report"] is None:
                render({"error": "loading"})
            else:
                render(vm.health_view(shown["report"]), refreshing=True)
            try:
                report = await get_backend().health()
            except Exception as exc:  # noqa: BLE001 - failure must not erase the previous result
                if not _client_alive(client):
                    return
                render({"error": f"{type(exc).__name__}: {exc}"})
                return
            if not _client_alive(client):
                return
            shown["report"] = report
            render(vm.health_view(report))

        render({"error": "loading"})
        background_tasks.create(load(), name="health-load")
        ui.button("Refresh health", icon="refresh", on_click=lambda: background_tasks.create(load(), name="health-refresh")).props(
            "outline dense"
        )


@ui.page("/")
def index():
    ui.navigate.to("/operations")


# ------------------------------------------------------- chunks/chunk/... ----

_GRID_COLUMNS = [
    {"field": "chunk", "headerName": "chunk", "pinned": "left", "width": 110},
    {"field": "account", "headerName": "account", "pinned": "left", "width": 160},
    {"field": "chunk_status", "headerName": "status", "width": 110},
    {"field": "selected", "headerName": "selected", "width": 100},
    {"field": "complete", "headerName": "complete", "width": 110},
    {"field": "rate", "headerName": "rate", "width": 100},
    {"field": "elapsed_s", "headerName": "elapsed_s", "width": 110},
    {"field": "vph", "headerName": "vph", "width": 110},
    {"field": "rpc9", "headerName": "rpc9", "width": 90},
    {"field": "degraded", "headerName": "degraded", "width": 110},
    {"field": "reasons", "headerName": "degradation reasons", "flex": 1},
]


@ui.page("/operations/chunks", response_timeout=RESPONSE_TIMEOUT_S)
async def chunks_page():
    with shell("Chunks × accounts"):
        client = context.client
        ui.label("source: monitor analyze_run · click a row to open the account investigation").classes(
            "text-slate-400 text-xs"
        )
        container = ui.column().classes("w-full")

        async def build():
            container.clear()
            payload = await get_backend().run_payload()
            rows = vm.chunks_rows(payload)
            if not _client_alive(client):
                return
            with container:
                if not rows:
                    ui.label("no chunk data available from the monitor").classes("text-amber-300")
                    return
                ui.label(f"{len(rows)} rows").classes("text-slate-400 text-xs")

                def on_cell(event):
                    data = event.args.get("data") if isinstance(event.args, dict) else None
                    if not data or data.get("account") is None or data.get("chunk") is None:
                        return
                    ui.navigate.to(f'/operations/chunk/{data["chunk"]}/account/{data["account"]}')

                # cellClicked (not rowClicked): the tested NiceGUI/AG Grid
                # combination did not deliver rowClicked events.
                ui.aggrid(
                    {
                        "columnDefs": _GRID_COLUMNS,
                        "rowData": rows,
                        "defaultColDef": {"sortable": True, "filter": True, "resizable": True},
                        "pagination": True,
                        "paginationPageSize": 100,
                    },
                    theme="balham",
                ).classes("w-full h-130").on("cellClicked", on_cell)

        await build()


@ui.page("/operations/chunk/{chunk}", response_timeout=RESPONSE_TIMEOUT_S)
async def chunk_page(chunk: int):
    with shell(f"Chunk {chunk:04d}"):
        view = vm.chunk_view(await get_backend().run_payload(), chunk)
        if not view.get("found"):
            _not_found(view)
            return
        with ui.row().classes("gap-8"):
            for label, value in [
                ("status", view.get("status")),
                ("completion", view.get("completion_rate")),
                ("wall_s", view.get("wall_s")),
                ("videos/hour", view.get("videos_per_hour")),
                ("rpc9", view.get("rpc9_add_errors")),
            ]:
                with ui.column().classes("gap-0"):
                    ui.label(str(value)).classes("text-xl font-mono")
                    ui.label(label).classes("text-slate-400 text-xs")
        ui.label("Accounts").classes("font-bold mt-2")
        for account in view.get("accounts", []):
            with ui.card().classes("bg-slate-900 w-full cursor-pointer").on(
                "click",
                lambda acct=account: ui.navigate.to(
                    f'/operations/chunk/{chunk}/account/{acct["account"]}'
                ),
            ):
                ui.label(
                    f'{account.get("account")} — rate {account.get("rate")} · '
                    f'{account.get("complete")}/{account.get("selected")} complete · '
                    f'degraded={account.get("degraded")}'
                ).classes("font-mono text-sm")


@ui.page("/operations/chunk/{chunk}/account/{account}", response_timeout=RESPONSE_TIMEOUT_S)
async def account_page(chunk: int, account: str):
    with shell(f"Chunk {chunk:04d} · {account}"):
        payload = await get_backend().run_payload()
        view = vm.account_view(payload, chunk, account)
        if not view.get("found"):
            _not_found(view)
            return
        with ui.row().classes("gap-3 flex-wrap"):
            ui.badge(f'status {view.get("status")}')
            ui.badge(f'rate {view.get("rate")}')
            ui.badge(f'rpc9 {view.get("rpc9_add_errors")}')
            ui.badge(
                f'degraded={view.get("degraded")}', color="red" if view.get("degraded") else "grey"
            )
        if view.get("degradation_reasons"):
            ui.label("degradation reasons: " + "; ".join(view["degradation_reasons"])).classes(
                "text-amber-300 font-mono text-xs"
            )
        stages = view.get("stages") or []
        if stages:
            ui.label("Stage latency — monitor-computed percentiles").classes("font-bold mt-2")
            ui.table(
                columns=[
                    {"name": k, "label": k, "field": k, "sortable": True}
                    for k in ("stage", "n", "p50", "p95", "max")
                ],
                rows=stages,
                row_key="stage",
            ).classes("w-full")
        ui.label("Drill into a video").classes("mt-4 font-bold")
        with ui.row().classes("gap-2"):
            video_input = ui.input(placeholder="video id").classes("w-64")
            ui.button(
                "Drill",
                on_click=lambda: ui.navigate.to(
                    f"/operations/chunk/{chunk}/video/{video_input.value or '?'}?account={account}"
                ),
            )


@ui.page("/operations/chunk/{chunk}/video/{video}", response_timeout=RESPONSE_TIMEOUT_S)
async def video_page(chunk: int, video: str, account: str = ""):
    with shell(f"Drill — chunk {chunk:04d} · {account or "auto"} · {video}"):
        payload = await get_backend().drill(chunk, account or None, video)
        view = vm.drill_view(payload)
        if view.get("error"):
            with ui.card().classes("bg-slate-900 w-full"):
                ui.icon("search_off", color="amber").classes("text-2xl")
                ui.label(view["error"]).classes("font-mono text-red-300")
                if view.get("note"):
                    ui.label(view["note"]).classes("text-slate-400 text-sm")
            _raw_json_drawer(payload, "Raw drill payload (fallback evidence)")
            return
        row = view.get("analysis_status_row") or {}
        cache = view.get("transcript_cache_row")
        with ui.row().classes("gap-3 flex-wrap"):
            ui.badge(f'status {row.get("status", "?")}')
            ui.badge(f'last_stage {row.get("last_stage", "?")}')
            if row.get("failure_reason"):
                ui.badge(f'failure: {row["failure_reason"][:60]}', color="red")
        if row.get("failure_reason"):
            ui.label(row["failure_reason"]).classes("font-mono text-xs text-red-300")
        if cache:
            ui.label(
                f'transcript cache: {cache.get("transcript_chars")} chars · source {cache.get("source")}'
            ).classes("text-slate-300 text-sm")
        if not view.get("found"):
            ui.label("no monitor evidence for this video in this chunk").classes("text-amber-300")
        with ui.card().classes("bg-slate-900 w-full"):
            ui.label(
                f'{view.get("event_count", len(view.get("events", [])))} events · run {view.get("run_id")}'
            ).classes("text-sm font-mono")
            if view.get("manifest_path"):
                ui.label(f'manifest: {view["manifest_path"]}').classes("text-slate-400 text-xs font-mono")
            if view.get("receipt_path"):
                ui.label(f'receipt: {view["receipt_path"]}').classes("text-slate-400 text-xs font-mono")
            if view.get("event_log_dir"):
                ui.label(f'events: {view["event_log_dir"]}').classes("text-slate-400 text-xs font-mono")
        events = view.get("events", [])
        if events:
            ui.table(
                columns=[
                    {"name": k, "label": k, "field": k, "sortable": True}
                    for k in ("timestamp", "action", "worker", "trace_id", "attempt", "elapsed_s", "status")
                ],
                rows=[
                    {k: (e.get("timestamp") or "")[11:23] if k == "timestamp" else e.get(k) for k in
                     ("timestamp", "action", "worker", "trace_id", "attempt", "elapsed_s", "status")}
                    for e in events
                ],
                row_key="action",
            ).classes("w-full")
        _raw_json_drawer(payload, "Raw drill payload (fallback evidence)")


def create_routes():  # pragma: no cover - routes register on import
    """Idempotent hook for tests: importing this module registers all pages."""
    return True
