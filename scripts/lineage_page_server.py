#!/usr/bin/env python3
"""Standalone live lineage page: sources -> pipelines -> index -> serving.

v2: font-size slider + day/night toggle (night = the original palette,
unchanged; day = light variant with matched contrast), clickable source
cards with per-source drill-down (recent docs), live port-liveness dots
for the serving faces, cross-page links, manual refresh + countdown.

Reads live counts (read-only) from the transcript cache, the canonical
batch DB, EF state, and the alert ledger; renders one self-refreshing
page on 127.0.0.1:6395. No writes anywhere; dies on reboot like the
other standalone page servers (relaunch: pythonw scripts/lineage_page_server.py).
"""
from __future__ import annotations

import json
import socket
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TC = "file:P:/.data/yt-is/transcripts.sqlite?mode=ro"
BS = "file:P:/.data/yt-is/batch_status.sqlite?mode=ro"
EF_STATE = Path("P:/.data/yt-is/ef/state.json")
OPEN_ALERTS = Path("P:/.data/yt-is/alerts/open.json")

# label -> (db sources feeding it, lane)
SOURCES = {
    "YouTube (NLM)": (["notebooklm"], "text"),
    "YouTube → Whisper recovery": (["whisper"], "audio"),
    "Discord (DHT)": (["discord", "dht-artifact"], "text"),
    "Reddit": (["reddit"], "text"),
    "RSS blogs": (["rss"], "text"),
    "Hacker News": (["hackernews"], "text"),
    "GitHub": (["github"], "text"),
    "Podcasts": (["podcast"], "audio"),
    "Email newsletters": (["newsletter"], "text"),
}
SERVING = [
    ("warm query + MCP", [6391, 8324]),
    ("search fleet", [8321, 8322, 8323]),
    ("interest graph", [6393]),
    ("Qdrant", [6390]),
    ("lineage", [6395]),
]


def _q(db_uri: str, sql: str, args=()):
    try:
        conn = sqlite3.connect(db_uri, uri=True, timeout=10)
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def _alive(port: int, cache_ttl: float = 30.0) -> bool:
    # 30s cache: a dead port costs the full socket timeout, and collect()
    # runs on every page load — 9 probes x 300ms would dominate render.
    now = time.monotonic()
    hit = _PROBE_CACHE.get(port)
    if hit and now - hit[0] < cache_ttl:
        return hit[1]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            up = True
    except OSError:
        up = False
    _PROBE_CACHE[port] = (now, up)
    return up


_PROBE_CACHE: dict[int, tuple[float, bool]] = {}


def collect() -> dict:
    since = datetime.fromtimestamp(time.time() - 7 * 86400, timezone.utc)\
        .strftime("%Y-%m-%dT%H:%M:%S")
    src_rows = dict(_q(TC, "select source, count(*) from transcript_cache "
                           "where cached_at >= ? group by source", (since,)))
    last_rows = dict(_q(TC, "select source, max(cached_at) from "
                            "transcript_cache where cached_at >= ? "
                            "group by source", (since,)))
    sources = {}
    for label, (dbs, lane) in SOURCES.items():
        docs = sum(src_rows.get(s, 0) for s in dbs)
        last = max((last_rows.get(s, "") or "" for s in dbs), default="")
        sources[label] = {"docs_7d": docs, "lane": lane,
                          "last": str(last)[:16]}
    # Unmapped DB sources stay visible (v1 fallback) so future connectors
    # never silently vanish from the lineage view.
    covered = {s for dbs, _ in SOURCES.values() for s in dbs}
    for s in sorted(set(src_rows) | set(last_rows) - covered):
        if s in covered:
            continue
        sources[s] = {"docs_7d": src_rows.get(s, 0), "lane": "text",
                      "last": str(last_rows.get(s, "") or "")[:16]}
    counts = dict(_q(BS, "select status, count(*) from analysis_status "
                         "group by status"))
    visual = dict(_q(BS, "select status, count(*) from visual_status "
                         "group by status"))
    ef: dict = {"lag": None, "last_success": "", "units": None}
    try:
        st = json.loads(EF_STATE.read_text(encoding="utf-8"))
        ef["lag"] = st.get("index_lag_count")
        ef["last_success"] = str(st.get("last_index_success", ""))[:16]
    except Exception:
        pass
    try:
        conn = sqlite3.connect(
            "file:P:/.data/yt-is/ef/catalog.sqlite?mode=ro", uri=True,
            timeout=10)
        ef["units"] = conn.execute("select count(*) from eu").fetchone()[0]
        conn.close()
    except Exception:
        pass
    alerts = 0
    try:
        ev = json.loads(OPEN_ALERTS.read_text(encoding="utf-8")).get("events")
        alerts = sum(1 for v in (ev or {}).values()
                     if isinstance(v, dict) and v.get("status") == "open")
    except Exception:
        pass
    serving = [{"name": n,
                "up": all(_alive(p) for p in ports),
                "ports": ",".join(str(p) for p in ports)}
               for n, ports in SERVING]
    return {"generated_at": datetime.now(timezone.utc).isoformat()[:19],
            "sources": sources, "analysis_status": counts,
            "visual": visual, "ef": ef, "open_alerts": alerts,
            "serving": serving}


def recent_docs(label: str, n: int = 5) -> list[dict]:
    dbs = SOURCES.get(label, ([], ""))[0]
    if not dbs:
        return []
    marks = ",".join("?" * len(dbs))
    rows = _q(TC, f"select source, cached_at, length(transcript), "
                  f"metadata_json from transcript_cache where source in "
                  f"({marks}) order by cached_at desc limit ?",
              (*dbs, n))
    out = []
    for src, at, ln, meta in rows:
        try:
            m = json.loads(meta or "{}")
        except Exception:
            m = {}
        title = (m.get("subject") or m.get("title")
                 or m.get("feed") or src)
        out.append({"title": str(title)[:90],
                    "date": str(at)[:16], "chars": ln})
    return out


STYLE = """
:root{--bg:#0f1115;--panel:#161a21;--panel2:#1c212b;--edge:#232a35;
--text:#dfe4ea;--dim:#8b98ab;--head:#9fb0c6;
--ok:#5cd68a;--warn:#e3b341;--off:#e05252;--link:#7ab7ff}
.day{--bg:#f2f4f8;--panel:#ffffff;--panel2:#eef1f6;--edge:#d5dbe4;
--text:#1a2230;--dim:#5a6577;--head:#3d4a5e;
--ok:#187a44;--warn:#8a6100;--off:#b3261e;--link:#0b57b8}
html{font-size:16px}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);
color:var(--text);margin:0;padding:18px;transition:background .2s,color .2s}
h1{font-size:1.15rem;margin:0 0 4px}.sub{color:var(--dim);font-size:.75rem;margin-bottom:12px}
.bar{display:flex;gap:12px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.bar label{font-size:.75rem;color:var(--dim)}
.btn{background:var(--panel2);color:var(--text);border:1px solid var(--edge);
border-radius:8px;padding:5px 12px;font-size:.78rem;cursor:pointer}
.btn:hover{border-color:var(--head)}
input[type=range]{accent-color:var(--head)}
.chip{font-size:.72rem;border-radius:10px;padding:2px 10px;border:1px solid var(--edge);color:var(--dim)}
.chip.alert{color:var(--off);border-color:var(--off)}
.cols{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
.lane{background:var(--panel);border:1px solid var(--edge);border-radius:10px;padding:12px}
.lane h2{font-size:.8rem;margin:0 0 8px;color:var(--head);text-transform:uppercase;letter-spacing:.5px}
.card{background:var(--panel2);border-radius:8px;padding:8px 10px;margin-bottom:8px}
.card.click{cursor:pointer}.card.click:hover{outline:1px solid var(--head)}
.card b{font-size:.8rem}.card .m{font-size:.7rem;color:var(--dim)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.off{color:var(--off)}
.docs{font-size:.72rem;color:var(--dim);margin-top:6px;line-height:1.5}
.docs div{border-top:1px dashed var(--edge);padding:3px 0}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
.serving{font-size:.75rem;color:var(--text);line-height:1.8}
.footer{color:var(--dim);font-size:.7rem;margin-top:14px}
"""

JS = """
function save(k,v){try{localStorage.setItem('lin-'+k,v)}catch(e){}}
function load(k){try{return localStorage.getItem('lin-'+k)}catch(e){return null}}
function applyFont(v){document.documentElement.style.fontSize=v+'px';
  document.getElementById('fsval').textContent=v+'px';save('font',v)}
function applyMode(m){document.body.classList.toggle('day',m==='day');
  document.getElementById('modelabel').textContent=m==='day'?'day':'night';
  save('mode',m)}
const f=load('font');if(f)applyFont(f);
const m=load('mode')||'night';applyMode(m);
let secs=60;
setInterval(()=>{secs--;if(secs<=0){location.reload();return}
  document.getElementById('countdown').textContent=secs},1000);
function esc(s){return String(s).replace(/[&<>"']/g,c=>({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function toggleDocs(label,el){
  const box=el.querySelector('.docs');
  if(box.style.display==='block'){box.style.display='none';return}
  box.textContent='loading…';box.style.display='block';
  fetch('/source?key='+encodeURIComponent(label)).then(r=>r.json())
    .then(d=>{box.innerHTML=(d.docs||[]).map(x=>
      `<div>${esc(x.date)} · ${(x.chars||0).toLocaleString()} ch · ${esc(x.title)}`)
      .join('')||'<div>no recent docs</div>'})
    .catch(()=>box.textContent='failed to load')}
"""


def _card(label: str, s: dict, clickable: bool = True) -> str:
    cls = "ok" if s["docs_7d"] > 0 else "off"
    click = ("onclick=\"toggleDocs(this.dataset.k,this)\" "
             f'data-k="{label}"' if clickable else "")
    return (f'<div class="card click" {click}><b>{label}</b> '
            f'<span class="{cls}">●</span>'
            f'<div class="m">{s["docs_7d"]:,} docs / 7d · '
            f'last {s["last"] or "—"}</div>'
            f'<div class="docs" style="display:none"></div></div>')


def render(d: dict) -> str:
    text_srcs = "".join(_card(k, v) for k, v in d["sources"].items()
                        if v["lane"] == "text")
    audio_srcs = "".join(_card(k, v) for k, v in d["sources"].items()
                         if v["lane"] == "audio")
    a = d["analysis_status"]
    vis_done = d["visual"].get("complete", 0)
    vis_total = vis_done + d["visual"].get("pending", 0)
    ef = d["ef"]
    units = f"{ef['units']:,}" if ef.get("units") else "—"
    serving_rows = "".join(
        f'<div>{"<span class=ok>●</span>" if s["up"] else "<span class=off>●</span>"} '
        f'{s["name"]} <span style="color:var(--dim)">:{s["ports"]}</span></div>'
        for s in d["serving"])
    alert_chip = (f'<span class="chip alert">{d["open_alerts"]} open alert(s)</span>'
                  if d["open_alerts"] else '<span class="chip">no open alerts</span>')
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>yt-is lineage</title><style>{STYLE}</style></head><body>
<h1>yt-is lineage — live</h1>
<div class="sub">generated {d['generated_at']} UTC · auto-refresh in
<span id="countdown">60</span>s · read-only</div>
<div class="bar">
{alert_chip}
<label>font <input type="range" min="12" max="24" value="16"
 oninput="applyFont(this.value)"> <span id="fsval">16px</span></label>
<button class="btn" onclick="applyMode(document.body.classList.contains('day')?'night':'day')">
◐ <span id="modelabel">night</span></button>
<button class="btn" onclick="location.reload()">refresh now</button>
</div>
<div class="cols">
<div class="lane"><h2>Text sources → text pipeline</h2>{text_srcs}</div>
<div class="lane"><h2>Audio sources → audio pipeline</h2>{audio_srcs}
<div class="card"><b>Bulk no-captions</b> <span class="off">●</span>
<div class="m">{a.get('pending', 0):,} rows parked (policy)</div></div>
<div class="lane" style="margin-top:8px"><h2>Video pipeline (derived from YouTube)</h2>
<div class="card"><b>Frames / OCR lane</b> <span class="off">●</span>
<div class="m">stopped — scorer NULL-thumbnail wave; {vis_total:,} lifetime jobs, {vis_done:,} done</div></div></div></div>
<div class="lane"><h2>Index + serving</h2>
<div class="card"><b>Evidence Fabric</b> <span class="ok">●</span>
<div class="m">{units} units · lag {ef.get('lag') if ef.get('lag') is not None else '—'} · indexed thru {ef.get('last_success') or '—'}</div></div>
<div class="serving">{serving_rows}</div>
<div class="serving" style="margin-top:6px">
<a href="http://127.0.0.1:6391/">search</a> ·
<a href="http://127.0.0.1:6391/digest">digest</a> ·
<a href="http://127.0.0.1:6391/dht">dht</a> ·
<a href="http://127.0.0.1:6391/graph">graph</a> ·
<a href="http://127.0.0.1:6393/today">today</a>
</div>
<div class="card" style="margin-top:8px"><b>Corpus</b>
<div class="m">{a.get('complete', 0):,} complete · {a.get('pending', 0):,} pending · {a.get('failed', 0):,} failed · {a.get('excluded', 0):,} excluded</div></div>
</div></div>
<div class="footer">click any source card for its recent docs ·
settings persist per browser</div>
<script>{JS}</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/data":
            body, ctype = json.dumps(collect()).encode(), "application/json"
        elif u.path == "/source":
            key = parse_qs(u.query).get("key", [""])[0]
            body = json.dumps({"docs": recent_docs(key)}).encode()
            ctype = "application/json"
        else:
            body, ctype = render(collect()).encode(), "text/html; charset=utf-8"
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
