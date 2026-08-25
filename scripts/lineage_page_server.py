#!/usr/bin/env python3
"""Standalone live lineage page: sources -> pipelines -> index -> serving.

Reads live counts (read-only) from the transcript cache, the canonical
batch DB, EF state, and Task Scheduler; renders one self-refreshing page
on 127.0.0.1:6395. No writes anywhere; dies on reboot like the other
standalone page servers (relaunch: pythonw scripts/lineage_page_server.py).
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TC = "file:P:/.data/yt-is/transcripts.sqlite?mode=ro"
BS = "file:P:/.data/yt-is/batch_status.sqlite?mode=ro"
EF_STATE = Path("P:/.data/yt-is/ef/state.json")

SOURCE_LABELS = {
    "notebooklm": ("YouTube (NLM)", "text"),
    "whisper": ("YouTube → Whisper recovery", "audio"),
    "discord": ("Discord (DHT)", "text"),
    "dht-artifact": ("Discord artifacts", "text"),
    "reddit": ("Reddit", "text"),
    "rss": ("RSS blogs", "text"),
    "hackernews": ("Hacker News", "text"),
    "github": ("GitHub", "text"),
    "podcast": ("Podcasts", "audio"),
    "newsletter": ("Email newsletters", "text"),
}


def _q(db_uri: str, sql: str, args=()):
    try:
        conn = sqlite3.connect(db_uri, uri=True, timeout=10)
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def collect() -> dict:
    week = (time.time() - 7 * 86400)
    since = datetime.fromtimestamp(week, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S")
    sources = {}
    for src, n in _q(TC, "select source, count(*) from transcript_cache "
                         "where cached_at >= ? group by source", (since,)):
        label, lane = SOURCE_LABELS.get(src, (src, "text"))
        if label in sources:
            sources[label]["docs_7d"] += n
        else:
            sources[label] = {"docs_7d": n, "lane": lane}
    last = {}
    for src, mx in _q(TC, "select source, max(cached_at) from transcript_cache "
                          "where cached_at >= ? group by source", (since,)):
        label, _ = SOURCE_LABELS.get(src, (src, "text"))
        last[label] = str(mx)[:16]
    for label, s in sources.items():
        s["last"] = last.get(label, "")
    counts = dict(_q(BS, "select status, count(*) from analysis_status "
                         "group by status"))
    visual = dict(_q(BS, "select status, count(*) from visual_status "
                         "group by status"))
    ef = {}
    try:
        st = json.loads(EF_STATE.read_text(encoding="utf-8"))
        ef = {"lag": st.get("index_lag_count"),
              "last_success": str(st.get("last_index_success", ""))[:16]}
    except Exception:
        pass
    eu = (_q(BS, "select 1") and None)  # placeholder, eu lives in EF catalog
    ef["units"] = None
    try:
        conn = sqlite3.connect("file:P:/.data/yt-is/ef/catalog.sqlite?mode=ro",
                               uri=True, timeout=10)
        ef["units"] = conn.execute("select count(*) from eu").fetchone()[0]
        conn.close()
    except Exception:
        pass
    return {"generated_at": datetime.now(timezone.utc).isoformat()[:19],
            "sources": sources, "analysis_status": counts,
            "visual": visual, "ef": ef}


STYLE = """
body{font-family:Segoe UI,system-ui,sans-serif;background:#0f1115;color:#dfe4ea;margin:0;padding:18px}
h1{font-size:18px;margin:0 0 4px}.sub{color:#7a8699;font-size:12px;margin-bottom:14px}
.cols{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
.lane{background:#161a21;border:1px solid #232a35;border-radius:10px;padding:12px}
.lane h2{font-size:13px;margin:0 0 8px;color:#9fb0c6;text-transform:uppercase;letter-spacing:.5px}
.card{background:#1c212b;border-radius:8px;padding:8px 10px;margin-bottom:8px}
.card b{font-size:13px}.card .m{font-size:11px;color:#8b98ab}
.ok{color:#5cd68a}.warn{color:#e3b341}.off{color:#e05252}
.serving{font-size:12px;color:#aeb9c9;line-height:1.7}
meta{animation:none}
"""


def render(d: dict) -> str:
    def src_card(label, s):
        cls = "ok" if s["docs_7d"] > 0 else "off"
        return (f'<div class="card"><b>{label}</b> '
                f'<span class="{cls}">●</span>'
                f'<div class="m">{s["docs_7d"]:,} docs / 7d · '
                f'last {s["last"] or "—"}</div></div>')

    text_srcs = "".join(src_card(k, v) for k, v in d["sources"].items()
                        if v["lane"] == "text")
    audio_srcs = "".join(src_card(k, v) for k, v in d["sources"].items()
                         if v["lane"] == "audio")
    a = d["analysis_status"]
    vis_open = d["visual"].get("pending", 0) or 0
    ef = d["ef"]
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>yt-is lineage</title><style>{STYLE}</style></head><body>
<h1>yt-is lineage — live</h1>
<div class="sub">generated {d['generated_at']} UTC · auto-refresh 60s · read-only</div>
<div class="cols">
<div class="lane"><h2>Text sources → text pipeline</h2>{text_srcs}</div>
<div class="lane"><h2>Audio sources → audio pipeline</h2>{audio_srcs}
<div class="card"><b>Bulk no-captions</b> <span class="off">●</span>
<div class="m">{a.get('pending', 0) - a.get('excluded', 0) + 0:,} rows parked (policy)</div></div>
<div class="lane" style="margin-top:8px"><h2>Video pipeline (derived from YouTube)</h2>
<div class="card"><b>Frames / OCR lane</b> <span class="off">●</span>
<div class="m">stopped — scorer NULL-thumbnail wave; {vis_open + d['visual'].get('complete', 0):,} lifetime jobs, {d['visual'].get('complete', 0):,} done</div></div></div></div>
<div class="lane"><h2>Index + serving</h2>
<div class="card"><b>Evidence Fabric</b> <span class="ok">●</span>
<div class="m">{(f"{ef['units']:,}" if ef.get('units') else '—')} units · lag {ef.get('lag') or '—'} · indexed thru {ef.get('last_success') or '—'}</div></div>
<div class="serving">
:warm query :6391 + MCP :8324<br>
:search fleet :8321-8323<br>
:interest graph :6393<br>
:daily digest + alerts<br>
:CHS federation (query-time)
</div>
<div class="card" style="margin-top:8px"><b>Corpus</b>
<div class="m">{a.get('complete', 0):,} complete · {a.get('pending', 0):,} pending · {a.get('failed', 0):,} failed · {a.get('excluded', 0):,} excluded</div></div>
</div></div></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data":
            body = json.dumps(collect()).encode()
            ctype = "application/json"
        else:
            body = render(collect()).encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 6395), H)
    print("lineage page on http://127.0.0.1:6395", flush=True)
    srv.serve_forever()
