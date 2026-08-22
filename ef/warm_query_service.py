"""Persistent warm EF query service — eliminates model cold-start per query.

Review F-5 (2026-08-19): bin/ef-query constructs BGEM3Dual() per invocation,
paying a full BGE-M3 reload (~5-15s) every call. This service holds one warm
ProductionQuery instance behind a localhost HTTP endpoint. The ef-query CLI
becomes a thin client with graceful fallback to in-process mode.

Usage:
    python -m ef.warm_query_service          # start the service (background)
    curl http://127.0.0.1:6391/query?q=test  # query
    curl http://127.0.0.1:6391/health        # readiness check
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote_plus

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HOST = "127.0.0.1"
PORT = int(os.environ.get("YTIS_EF_QUERY_PORT", "6391"))
PID_FILE = REPO / ".data" / "yt-is" / "ef" / "query-service.pid"

_query_instance = None
_query_lock = threading.Lock()


def get_query():
    """Lazy singleton for the warm ProductionQuery."""
    global _query_instance
    if _query_instance is None:
        with _query_lock:
            if _query_instance is None:
                from ef import embedding, buildspec
                from ef.query_server import ProductionQuery

                _query_instance = ProductionQuery(
                    embedding.BGEM3Dual(),
                    buildspec.load_spec()["generation"],
                )
    return _query_instance


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            try:
                q = get_query()
                self._json(200, {"status": "ready", "model": "warm"})
            except Exception as e:
                self._json(503, {"status": "warming", "error": str(e)[:100]})

        elif parsed.path == "/candidates/approve":
            # parse_qs/quote_plus come from the module import: a local
            # import here would shadow them for the whole of do_GET and
            # crash every earlier route with UnboundLocalError.
            params = parse_qs(parsed.query)
            name = (params.get("name") or [""])[0]
            try:
                cj = Path("P:/.data/yt-is/ef/channel-candidates.json")
                cd = json.loads(cj.read_text(encoding="utf-8"))
                hits = 0
                for c in cd.get("candidates", []):
                    if not name or c["name"] == name:
                        c["status"] = "approved"; hits += 1
                cd["approved_by"] = f"home-page click {time.strftime('%Y-%m-%d')} name={name or 'ALL'}"
                cj.write_text(json.dumps(cd, indent=1, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                self._text(500, f"approve failed: {e}"); return
            self.send_response(302)
            self.send_header("Location", "/home")
            self.end_headers()
            return
        elif parsed.path == "/query":
            params = parse_qs(parsed.query)
            query_text = params.get("q", [""])[0]
            if not query_text:
                self._json(400, {"error": "missing q parameter"})
                return
            top_k = int(params.get("top_k", ["8"])[0])
            channel_id = params.get("channel_id", [None])[0]
            fmt = params.get("format", ["json"])[0]

            try:
                q = get_query()
                results = q.relevant(query_text, limit=top_k, channel_id=channel_id)
                if fmt == "text":
                    lines = []
                    for r in results:
                        lines.append(f"- {r.title}")
                        lines.append(f"  {r.snippet[:200]}")
                        if r.url:
                            lines.append(f"  {r.url}")
                        lines.append("")
                    self._text(200, "\n".join(lines))
                else:
                    payload = [{
                        "chunk_id": r.chunk_id,
                        "eu_id": r.eu_id,
                        "video_id": r.video_id,
                        "title": r.title,
                        "channel_id": r.channel_id,
                        "channel_title": r.channel_title,
                        "snippet": r.snippet,
                        "score": float(r.score),
                        "retrieval_paths": list(r.retrieval_paths),
                        "url": r.url,
                        "start_char": r.start_char,
                        "end_char": r.end_char,
                        "source_type": "corpus",
                    } for r in results]
                    # CHS federation: conversation history as a search leg.
                    # Federated rows carry no reopen provenance, so strict
                    # authority consumers (YT Workspace) opt out with
                    # federation=off instead of filtering client-side.
                    if params.get("federation", ["on"])[0] != "off":
                        try:
                            payload.extend(_chs_search(query_text, 3))
                        except Exception:
                            pass
                    self._json(200, {
                        "results": payload
                    })
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})

        elif parsed.path == "/library":
            # Read-only library membership for the extension header state.
            # Presence only: never returns transcript content.
            params = parse_qs(parsed.query)
            video_id = (params.get("video_id") or [""])[0]
            if not video_id or len(video_id) > 64:
                self._json(400, {"error": "missing or invalid video_id"})
                return
            try:
                self._json(200, library_lookup(video_id))
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})

        elif parsed.path == "/reopen":
            # Exact authoritative span reopen from provenance. Malformed
            # requests are 400; unknown evidence is 404. The text length is
            # exactly end_char - start_char or the reopen fails closed.
            params = parse_qs(parsed.query)
            eu_id = (params.get("eu_id") or [""])[0]
            try:
                start = int((params.get("start_char") or ["-1"])[0])
                end = int((params.get("end_char") or ["-1"])[0])
            except ValueError:
                start, end = -1, -1
            if (not eu_id or len(eu_id) > 256 or start < 0 or end <= start
                    or end - start > 64 * 1024):
                self._json(400, {"error": "malformed reopen request"})
                return
            try:
                result = reopen_exact(eu_id, start, end)
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
                return
            if result is None:
                self._json(404, {"error": "evidence unit or authority span not found"})
                return
            self._json(200, result)


        elif parsed.path == "/" or parsed.path == "/search":
            # Serve the search page from the same origin as the API:
            # a file:// page calling http://127.0.0.1 is blocked by
            # Chromium's Local/Private Network Access rules regardless of
            # CORS headers, surfacing as "Failed to fetch".
            try:
                page = (REPO / "docs" / "search.html").read_bytes()
                self._bytes(200, page, "text/html; charset=utf-8")
            except OSError as e:
                self._text(500, f"search.html unavailable: {e}")

        elif parsed.path == "/digest":
            try:
                self._bytes(200, _render_digest_page().encode("utf-8"),
                            "text/html; charset=utf-8")
            except Exception as e:
                self._text(500, f"digest unavailable: {e}")

        elif parsed.path == "/topics":
            import sqlite3
            conn = sqlite3.connect(
                f"file:{Path('P:/.data/yt-is/ef/catalog.sqlite')}?mode=ro",
                uri=True, timeout=10.0)
            try:
                rows = conn.execute("""
                    SELECT label, video_count FROM topic_clusters
                    WHERE member_count > 0 ORDER BY member_count DESC LIMIT 10
                """).fetchall()
            finally:
                conn.close()
            self._json(200, {"topics": [
                {"label": l, "videos": v} for l, v in rows]})

        elif parsed.path == "/review":
            page = REPO / ".logs" / "channel_review" / "review.html"
            try:
                self._bytes(200, page.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._text(
                    503, "The YouTube channel review page hasn't been "
                    "generated yet. Run:  ytis review")

        elif parsed.path == "/reddit":
            try:
                self._bytes(200, _render_source_page("reddit").encode("utf-8"),
                            "text/html; charset=utf-8")
            except Exception as e:
                self._text(500, f"reddit page unavailable: {e}")

        elif parsed.path == "/discord":
            try:
                self._bytes(200, _render_source_page("discord").encode("utf-8"),
                            "text/html; charset=utf-8")
            except Exception as e:
                self._text(500, f"discord page unavailable: {e}")

        elif parsed.path == "/ask":
            params2 = parse_qs(parsed.query)
            q = params2.get("q", [""])[0]
            if q:
                from ef import qa
                self._json(200, qa.answer(q))
            else:
                try:
                    self._bytes(200, _render_ask_page().encode("utf-8"),
                                "text/html; charset=utf-8")
                except Exception as e:
                    self._text(500, f"ask page unavailable: {e}")

        elif parsed.path == "/home":
            try:
                self._bytes(200, _render_home_page().encode("utf-8"),
                            "text/html; charset=utf-8")
            except Exception as e:
                self._text(500, f"home unavailable: {e}")

        elif parsed.path == "/entities":
            try:
                self._bytes(200, _render_entities_page().encode("utf-8"),
                            "text/html; charset=utf-8")
            except Exception as e:
                self._text(500, f"entities page unavailable: {e}")

        elif parsed.path == "/sources":
            try:
                self._bytes(200, _render_sources_page().encode("utf-8"),
                            "text/html; charset=utf-8")
            except Exception as e:
                self._text(500, f"sources page unavailable: {e}")

        elif parsed.path == "/status":
            try:
                self._bytes(200, _render_status_page().encode("utf-8"),
                            "text/html; charset=utf-8")
            except Exception as e:
                self._text(500, f"status unavailable: {e}")

        elif parsed.path == "/trends":
            try:
                self._json(200, _topic_trends())
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})

        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        import sqlite3
        try:
            if parsed.path == "/sources/rss/add":
                url = params.get("url", [""])[0].strip()
                if not url.startswith("http"):
                    return self._json(400, {"error": "url required"})
                conn = sqlite3.connect(
                    str(Path("P:/.data/yt-is/batch_status.sqlite")),
                    timeout=30.0)
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("""CREATE TABLE IF NOT EXISTS rss_feeds (
                    url TEXT PRIMARY KEY, name TEXT, added_at TEXT NOT NULL,
                    last_synced TEXT, etag TEXT, last_modified TEXT,
                    total_entries INTEGER DEFAULT 0)""")
                conn.execute(
                    "INSERT OR IGNORE INTO rss_feeds (url, name, added_at) "
                    "VALUES (?, ?, datetime('now'))", (url, url))
                conn.commit(); conn.close()
                return self._json(200, {"ok": True})
            if parsed.path == "/sources/rss/remove":
                url = params.get("url", [""])[0]
                conn = sqlite3.connect(
                    str(Path("P:/.data/yt-is/batch_status.sqlite")),
                    timeout=30.0)
                conn.execute("DELETE FROM rss_feeds WHERE url = ?", (url,))
                conn.commit(); conn.close()
                return self._json(200, {"ok": True})
            if parsed.path == "/sources/podcast/add":
                url = params.get("url", [""])[0].strip()
                name = params.get("name", [""])[0].strip() or url
                if not url.startswith("http"):
                    return self._json(400, {"error": "feed url required"})
                conn = sqlite3.connect(
                    str(Path("P:/.data/yt-is/batch_status.sqlite")), timeout=30.0)
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("""CREATE TABLE IF NOT EXISTS podcast_feeds (
                    url TEXT PRIMARY KEY, name TEXT,
                    added_at TEXT NOT NULL, last_synced TEXT)""")
                conn.execute(
                    "INSERT OR IGNORE INTO podcast_feeds (url, name, added_at) "
                    "VALUES (?, ?, datetime('now'))", (url, name))
                conn.commit(); conn.close()
                return self._json(200, {"ok": True})
            if parsed.path == "/sources/podcast/remove":
                url = params.get("url", [""])[0]
                conn = sqlite3.connect(
                    str(Path("P:/.data/yt-is/batch_status.sqlite")), timeout=30.0)
                conn.execute("DELETE FROM podcast_feeds WHERE url = ?", (url,))
                conn.commit(); conn.close()
                return self._json(200, {"ok": True})
            if parsed.path == "/sources/reddit/add":
                name = params.get("name", [""])[0].strip().removeprefix("r/")
                if not name:
                    return self._json(400, {"error": "name required"})
                conn = sqlite3.connect(
                    str(Path("P:/.data/yt-is/batch_status.sqlite")),
                    timeout=30.0)
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("""CREATE TABLE IF NOT EXISTS reddit_subreddits (
                    subreddit TEXT PRIMARY KEY, added_at TEXT NOT NULL,
                    last_synced TEXT)""")
                conn.execute(
                    "INSERT OR IGNORE INTO reddit_subreddits (subreddit, "
                    "added_at) VALUES (?, datetime('now'))", (name,))
                conn.commit(); conn.close()
                return self._json(200, {"ok": True})
            if parsed.path == "/sources/reddit/remove":
                name = params.get("name", [""])[0].removeprefix("r/")
                conn = sqlite3.connect(
                    str(Path("P:/.data/yt-is/batch_status.sqlite")),
                    timeout=30.0)
                conn.execute("DELETE FROM reddit_subreddits WHERE "
                             "subreddit = ?", (name,))
                conn.commit(); conn.close()
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})
        except Exception as e:
            return self._json(500, {"error": str(e)[:200]})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def _json(self, code, data):
        body = json.dumps(data, indent=1).encode("utf-8")
        self._bytes(code, body, "application/json")

    def _text(self, code, text):
        self._bytes(code, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _bytes(self, code, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # CORS for any remaining file://-opened consumers, plus the PNA
        # opt-in Chromium asks about for localhost targets
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress default request logging


def _topic_trends() -> dict:
    """Topic momentum: most new content and biggest % change per window.

    New content per topic counts chunk assignments whose assigned_at (the
    transcript's capture time) falls in the window; % change compares the
    window to the equal-length window before it, floored at MIN_VOLUME
    chunks so a 3→9 blip can't outrank real movement.
    """
    import sqlite3
    from datetime import datetime, timedelta, timezone

    MIN_VOLUME = 25
    TOP_N = 8
    windows = {"24h": 1, "72h": 3, "7d": 7}
    now = datetime.now(timezone.utc)

    conn = sqlite3.connect(
        f"file:{Path('P:/.data/yt-is/ef/catalog.sqlite')}?mode=ro", uri=True,
        timeout=10.0)
    try:
        out = {}
        for name, days in windows.items():
            cur_start = (now - timedelta(days=days)).isoformat()
            prev_start = (now - timedelta(days=days * 2)).isoformat()
            cur = dict(conn.execute("""
                SELECT tc.cluster_id, tc.label FROM topic_clusters tc
                WHERE tc.cluster_id != -1
                  AND COALESCE(tc.is_series, 0) = 0
            """).fetchall())
            counts_cur = dict(conn.execute("""
                SELECT cc.cluster_id, COUNT(*) FROM chunk_clusters cc
                JOIN topic_clusters tc ON tc.cluster_id = cc.cluster_id
                WHERE cc.assigned_at >= ? AND COALESCE(tc.is_series, 0) = 0
                GROUP BY cc.cluster_id
            """, (cur_start,)).fetchall())
            counts_prev = dict(conn.execute("""
                SELECT cc.cluster_id, COUNT(*) FROM chunk_clusters cc
                JOIN topic_clusters tc ON tc.cluster_id = cc.cluster_id
                WHERE cc.assigned_at >= ? AND cc.assigned_at < ?
                  AND COALESCE(tc.is_series, 0) = 0
                GROUP BY cc.cluster_id
            """, (prev_start, cur_start)).fetchall())

            def label(cid):
                return cur.get(cid, f"topic {cid}")

            most_new = sorted(
                ((label(cid), n) for cid, n in counts_cur.items()),
                key=lambda x: -x[1])[:TOP_N]

            changes = []
            for cid, n in counts_cur.items():
                if n < MIN_VOLUME:
                    continue
                p = counts_prev.get(cid, 0)
                if p == 0:
                    pct = 999.0        # new arrival: no baseline
                else:
                    pct = (n - p) / p * 100.0
                changes.append((label(cid), pct, n, p))
            changes.sort(key=lambda x: -x[1])
            out[name] = {
                "most_new": [{"topic": t, "new_chunks": n}
                             for t, n in most_new],
                "biggest_change": [
                    {"topic": t, "pct": round(p, 1), "current": n,
                     "previous": prev}
                    for t, p, n, prev in changes[:TOP_N]],
            }
        return out
    finally:
        conn.close()


def _render_ask_page() -> str:
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ytis — Ask</title>
<style>
  body { font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
        background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }
  h1 { color: #58a6ff; } a { color: #58a6ff; text-decoration: none; }
  input { width: 70%; background: #161b22; color: #e6edf3;
         border: 1px solid #30363d; border-radius: 8px;
         padding: .8rem 1rem; font-size: 1.05rem; }
  button { background: #1f6feb; color: #fff; border: none;
          border-radius: 8px; padding: .8rem 1.6rem; font-size: 1.05rem;
          cursor: pointer; }
  .answer { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 1.2rem 1.5rem; margin: 1.5rem 0; line-height: 1.6;
           white-space: pre-wrap; max-width: 900px; }
  .src { border-left: 3px solid #30363d; padding: .4rem .8rem; margin: .5rem 0;
        font-size: .88rem; color: #8b949e; max-width: 860px; }
  .src a { color: #58a6ff; } .dim { color: #8b949e; }
</style></head><body>
<nav><a href="/">Search</a> · <a href="/home">Home</a> ·
<a href="/digest">Daily brief</a> · <a href="/status">Status</a></nav>
<h1>Ask your knowledge base</h1>
<p class="dim">Answers from 208K+ transcripts, posts, and articles — with citations.</p>
<p><input id="q" placeholder="e.g., What do my sources say about GLM-5?"
        autofocus><button onclick="ask()">Ask</button></p>
<div id="out"></div>
<script>
const out = document.getElementById('out');
document.getElementById('q').addEventListener('keydown',
    e => { if (e.key === 'Enter') ask(); });
async function ask() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  out.innerHTML = '<p class="dim">Thinking… (retrieving + answering)</p>';
  try {
    const r = await fetch('/ask?q=' + encodeURIComponent(q));
    const d = await r.json();
    out.innerHTML = '<div class="answer">' + d.answer +
      '</div><p class="dim">answered by: ' + (d.provider || '?') + '</p>' +
      (d.sources || []).map((s, i) =>
        '<div class="src">[' + (i+1) + '] <a href="' + s.url + '">' +
        (s.title || s.url).slice(0, 90) + '</a><br>' +
        (s.snippet || '').slice(0, 160) + '…</div>').join('');
  } catch (e) { out.innerHTML = '<p class="dim">failed: ' + e + '</p>'; }
}
</script>
</body></html>"""


def _channel_side_published(days: int) -> int:
    """New videos actually PUBLISHED by tracked YouTube channels in the
    window — upstream channel activity, not our ingestion volume.
    channel_id IS NOT NULL excludes connector docs (reddit/hn/rss rows
    carry no channel and get published_at stamped at ingest time)."""
    import sqlite3
    from ef import authority
    conn = sqlite3.connect(f"file:{authority.STATUS_DB}?mode=ro", uri=True)
    try:
        n = conn.execute(
            "select count(*) from analysis_status "
            "where channel_id is not null and published_at is not null "
            "and julianday(published_at) > julianday('now', ?)",
            (f"-{days} days",)).fetchone()[0]
    finally:
        conn.close()
    return n or 0


def _render_home_page() -> str:
    """The unified glance dashboard: brief numbers + topic momentum +
    source cards + health, one screen."""
    import sqlite3
    from datetime import datetime, timezone
    from urllib.parse import quote_plus

    today = None
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO / "scripts"))
        import generate_digest as gd
        today = gd.get_new_transcripts(24)
        stats = gd.get_stats()
    except Exception:
        today, stats = {"total": 0, "channels": []}, {}

    trends = _topic_trends()
    cards_24 = trends.get("24h", {})

    def trend_table(items):
        return "".join(
            f"<tr><td>{x['topic'][:34]}</td><td class='num'>{x['new_chunks']:,}</td></tr>"
            for x in (items or [])[:5])

    def chg_table(items):
        return "".join(
            f"<tr><td>{x['topic'][:34]}</td>"
            f"<td class='num up'>+{x['pct']}%</td></tr>"
            if x["pct"] != 999 else
            f"<tr><td>{x['topic'][:34]}</td><td class='num'>new</td></tr>"
            for x in (items or [])[:5])

    # Today's trend alerts: topics whose momentum crosses the operator
    # threshold (default +200% and >=50 new chunks). Read from the catalog
    # that compute_trend_alerts.py wrote in the 06:00 task. Empty list
    # is normal — the panel collapses out of the page when there's
    # nothing to surface, so quiet days look like the old /home.
    today_alerts: list[dict] = []
    try:
        import compute_trend_alerts as cta
        today_alerts = cta.get_today_alerts()
    except Exception:
        today_alerts = []

    def alert_table(items):
        return "".join(
            f"<tr><td><a href='/query?q={quote_plus(x['topic'])}'>"
            f"{x['topic'][:40]}</a></td>"
            f"<td class='num dim'>{x['window']}</td>"
            f"<td class='num up'>"
            f"{('+' + str(x['pct']) + '%') if x['pct'] != 999 else 'new'}"
            f"</td>"
            f"<td class='num'>{x['new_chunks']:,}</td></tr>"
            for x in (items or [])[:8])

    alerts_html = ""
    if today_alerts:
        alerts_html = (
            f"<div class='panel' style='border-color: #d29922;'>"
            f"<h3>Today's Alerts &mdash; {len(today_alerts)}</h3>"
            f"<table>{alert_table(today_alerts)}</table>"
            f"<p class='dim'>Click a topic to search the corpus.</p></div>"
        )

    conn = sqlite3.connect(
        f"file:{Path('P:/.data/yt-is/transcripts.sqlite')}?mode=ro",
        uri=True, timeout=10.0)
    try:
        docs = dict(conn.execute(
            "SELECT source, COUNT(*) FROM transcript_cache "
            "WHERE source IN ('reddit','hackernews','rss','discord') "
            "GROUP BY source").fetchall())
    finally:
        conn.close()

    now = datetime.now(timezone.utc)

    # Corpus-breadth candidate panel (D4 corpus lever): the operator's
    # always-visible "what to add next" list. Collapses when no candidates.
    candidates_html = ""
    try:
        cc = json.loads(
            Path("P:/.data/yt-is/ef/channel-candidates.json").read_text(
                encoding="utf-8"))
        rows_html = "".join(
            f"<tr><td>{c['name']}</td><td class='dim'>{c['domain']}</td>"
            f"<td class='dim'>{c.get('why', '')[:70]}</td>"
            f"<td class='num'>{'&#9989;' if c.get('status') == 'approved' else "<a href='/candidates/approve?name=" + c['name'] + "'>&#9744; approve</a>"}</td></tr>"
            for c in cc.get("candidates", []))
        cov_rows = "".join(
            f"<tr><td>{d}</td><td class='num'>{v['videos']:,}</td>"
            f"<td class='num'>{v['channels']:,}</td></tr>"
            for d, v in list(cc.get("coverage", {}).items())[:7])
        if rows_html:
            candidates_html = (
                '<div class="panel" style="flex-basis:100%"><h3>Corpus '
                'coverage &mdash; and candidate channels to add (approve by '
                'editing P:/.data/yt-is/ef/channel-candidates.json: '
                'status &rarr; approved)</h3><div class="grid">'
                '<div style="flex:1;min-width:280px"><table>'
                f"{rows_html}</table></div>"
                '<div style="flex:1;min-width:220px"><h3 class="dim">'
                'current coverage by domain</h3>'
                f"<table>{cov_rows}</table></div></div></div>")
    except Exception:
        candidates_html = ""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ytis — Home</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
        background: #0d1117; color: #e6edf3; margin: 0; padding: 1.5rem; }}
  h1 {{ color: #58a6ff; margin-bottom: 0; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
          padding: .9rem 1.2rem; min-width: 150px; }}
  .card .v {{ font-size: 1.5rem; font-weight: 700; color: #58a6ff; }}
  .grid {{ display: flex; gap: 1rem; flex-wrap: wrap; align-items: stretch; }}
  .panel {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 1rem; flex: 1; min-width: 280px; }}
  .panel h3 {{ color: #8b949e; font-size: .85rem; text-transform: uppercase;
              letter-spacing: .05em; margin-top: 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .88rem; }}
  td {{ padding: .25rem .3rem; border-bottom: 1px solid #21262d; }}
  td.num {{ text-align: right; color: #8b949e; font-variant-numeric: tabular-nums; }}
  .up {{ color: #3fb950; }}
  .dim {{ color: #8b949e; }}
</style></head><body>
<nav><a href="/">Search</a> · <b>Home</b> · <a href="/digest">Daily brief</a> ·
<a href="/sources">Sources</a> · <a href="/review">YouTube channels</a> ·
<a href="/ask">Ask</a> · <a href="/status">Status</a></nav>
<h1>Good {('morning' if now.hour < 12 else 'afternoon' if now.hour < 18 else 'evening')}</h1>
<p class="dim">{now.strftime('%A, %Y-%m-%d %H:%M')} UTC</p>

<div class="cards">
  <div class="card"><div class="v">{_channel_side_published(1):,}</div>new on channels (24h)</div>
  <div class="card"><div class="v">{today['total']:,}</div>transcripts ingested (24h)</div>
  <div class="card"><div class="v">{len(today['channels'])}</div>active channels</div>
  <div class="card"><div class="v">{stats.get('complete', 0):,}</div>total in corpus</div>
  <div class="card"><div class="v">{docs.get('reddit', 0) + docs.get('hackernews', 0) + docs.get('rss', 0):,}</div>community docs</div>
</div>

{alerts_html}

<div class="grid">
  <div class="panel"><h3>Most new content — 24h</h3>
    <table>{trend_table(cards_24.get('most_new'))}</table></div>
  <div class="panel"><h3>Rising — 24h</h3>
    <table>{chg_table(cards_24.get('biggest_change'))}</table></div>
  <div class="panel"><h3>Sources</h3>
    <table>
      <tr><td>YouTube</td><td class="num">{stats.get('channels', 0):,} channels</td></tr>
      <tr><td>Reddit</td><td class="num">{docs.get('reddit', 0):,} docs</td></tr>
      <tr><td>Hacker News</td><td class="num">{docs.get('hackernews', 0):,} docs</td></tr>
      <tr><td>RSS feeds</td><td class="num">{docs.get('rss', 0):,} docs</td></tr>
      <tr><td>Discord</td><td class="num">{docs.get('discord', 0):,} docs</td></tr>
    </table>
    <p class="dim"><a href="/sources">manage sources →</a></p></div>
</div>

{candidates_html}

<p class="dim">Full brief: <a href="/digest">daily brief + 7-day view</a> ·
momentum over 72h/7d: <a href="/">search page</a></p>
</body></html>"""


def _render_entities_page() -> str:
    """Named entities across the corpus with cross-source chunk counts."""
    import sqlite3
    conn = sqlite3.connect(
        f"file:{Path('P:/.data/yt-is/ef/catalog.sqlite')}?mode=ro",
        uri=True, timeout=10.0)
    try:
        top = conn.execute("""
            SELECT ec.entity, ec.label, ec.chunk_count
            FROM entity_corpus ec ORDER BY ec.chunk_count DESC LIMIT 25
        """).fetchall()
        by_type = conn.execute("""
            SELECT ec.label, COUNT(*) FROM entity_corpus ec
            GROUP BY ec.label ORDER BY 2 DESC
        """).fetchall()
        per_topic = conn.execute("""
            SELECT tc.label, GROUP_CONCAT(e.entity, ' · ') FROM (
                SELECT cluster_id, entity FROM entities
                ORDER BY mentions DESC) e
            JOIN topic_clusters tc ON tc.cluster_id = e.cluster_id
            GROUP BY tc.cluster_id ORDER BY tc.member_count DESC LIMIT 8
        """).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM entity_corpus").fetchone()[0]
        extracted = conn.execute(
            "SELECT COUNT(*) FROM entities").fetchone()[0]
    finally:
        conn.close()

    def rows(items):
        return "".join(
            f"<tr><td class='topic-link' onclick=\"askSearch('{e}')\">{e}</td>"
            f"<td class='dim'>{l}</td><td class='num'>{n:,}</td></tr>"
            for e, l, n in items)

    import html as _h
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ytis — Entities</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
        background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }}
  h1, h2 {{ color: #58a6ff; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  table {{ border-collapse: collapse; width: 100%; margin: .8rem 0 1.5rem; }}
  th, td {{ text-align: left; padding: .45rem .7rem;
           border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; }} td.num {{ text-align: right; }}
  .dim {{ color: #8b949e; }}
  .topic-link {{ color: #58a6ff; cursor: pointer; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: .5rem; margin-bottom: 1.5rem; }}
  .chip {{ background: #21262d; border: 1px solid #30363d; border-radius: 14px;
          padding: .2rem .7rem; font-size: .85rem; }}
</style>
<script>function askSearch(e) {{ window.open('/?q=' + encodeURIComponent(e), '_self'); }}</script>
</head><body>
<nav><a href="/">&larr; Search</a> · <a href="/home">Home</a> ·
<a href="/digest">Daily brief</a> · <a href="/status">Status</a></nav>
<h1>Entities</h1>
<p class="dim">{extracted:,} entities extracted across topics ·
{total:,} with corpus counts · click any entity to search it</p>

<h2>Most present in the corpus</h2>
<table><tr><th>Entity</th><th>Type</th><th>Chunks</th></tr>{rows(top)}</table>

<h2>By type</h2>
<div class="chips">{"".join(f'<span class="chip">{_h.escape(l)}: {n}</span>' for l, n in by_type)}</div>

<h2>Key entities per topic</h2>
{"".join(f'<p><b>{_h.escape(lbl[:50])}</b><br><span class="dim">{_h.escape(ents[:300])}</span></p>' for lbl, ents in per_topic)}
</body></html>"""


def _render_sources_page() -> str:
    """Config page: add/remove tracked feeds and subreddits in the browser."""
    import sqlite3
    conn = sqlite3.connect(
        f"file:{Path('P:/.data/yt-is/batch_status.sqlite')}?mode=ro",
        uri=True, timeout=10.0)
    feeds = conn.execute(
        "SELECT url, COALESCE(name, url), total_entries FROM rss_feeds "
        "ORDER BY added_at").fetchall()
    subs = conn.execute(
        "SELECT subreddit FROM reddit_subreddits ORDER BY added_at"
    ).fetchall()
    try:
        pods = conn.execute(
            "SELECT url, COALESCE(name, url) FROM podcast_feeds "
            "ORDER BY added_at").fetchall()
    except sqlite3.OperationalError:
        pods = []
    conn.close()

    feed_rows = "".join(
        f"<tr><td>{f[1][:60]}</td><td class='dim'>{f[0][:70]}</td>"
        f"<td class='num'>{f[2] or 0}</td>"
        f"<td><button onclick=\"rmFeed('{f[0]}')\">remove</button></td></tr>"
        for f in feeds)
    sub_rows = "".join(
        f"<tr><td>r/{s[0]}</td>"
        f"<td><button onclick=\"rmSub('{s[0]}')\">remove</button></td></tr>"
        for s in subs)
    pod_rows = "".join(
        f"<tr><td>{pf[1]}</td><td class='dim'>{pf[0][:70]}</td>"
        f"<td><button onclick=\"rmPod('{pf[0]}')\">remove</button></td></tr>"
        for pf in pods)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ytis — Sources</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
        background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }}
  h1, h2 {{ color: #58a6ff; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  table {{ border-collapse: collapse; width: 100%; margin: .8rem 0 1.5rem; }}
  th, td {{ text-align: left; padding: .45rem .7rem;
           border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; }} td.num {{ text-align: right; }}
  input {{ background: #161b22; color: #e6edf3; border: 1px solid #30363d;
          border-radius: 6px; padding: .5rem .8rem; font-size: 1rem;
          width: 60%; }}
  button {{ background: #21262d; color: #e6edf3; border: 1px solid #30363d;
           border-radius: 6px; padding: .35rem .9rem; cursor: pointer; }}
  button:hover {{ border-color: #58a6ff; }}
  .dim {{ color: #8b949e; }}
</style></head><body>
<nav><a href="/">&larr; Search</a> · <a href="/home">Home</a> ·
<a href="/digest">Daily brief</a> · <a href="/status">Status</a></nav>
<h1>Sources</h1>

<h2>RSS feeds</h2>
<table><tr><th>Feed</th><th>URL</th><th>Entries</th><th></th></tr>{feed_rows}</table>
<p><input id="feedurl" placeholder="https://example.com/feed.xml">
<button onclick="addFeed()">Add feed</button>
<span class="dim"> — synced daily at 06:00, or run <code>ytis rss</code></span></p>

<h2>Podcasts</h2>
<table><tr><th>Show</th><th>Feed URL</th><th></th></tr>{pod_rows}</table>
<p><input id="podname" placeholder="Show name" style="width:22%">
<input id="podurl" placeholder="RSS feed URL" style="width:45%">
<button onclick="addPod()">Add podcast</button>
<span class="dim"> — episodes transcribed locally (Whisper)</span></p>

<h2>Reddit subreddits</h2>
<table><tr><th>Subreddit</th><th></th></tr>{sub_rows}</table>
<p><input id="subname" placeholder="LocalLLaMA" style="width:30%">
<button onclick="addSub()">Add subreddit</button></p>

<script>
async function addFeed() {{
  const url = document.getElementById('feedurl').value.trim();
  if (!url) return;
  await fetch('/sources/rss/add?url=' + encodeURIComponent(url), {{method: 'POST'}});
  location.reload();
}}
async function rmFeed(url) {{
  await fetch('/sources/rss/remove?url=' + encodeURIComponent(url), {{method: 'POST'}});
  location.reload();
}}
async function addSub() {{
  const name = document.getElementById('subname').value.trim();
  if (!name) return;
  await fetch('/sources/reddit/add?name=' + encodeURIComponent(name), {{method: 'POST'}});
  location.reload();
}}
async function rmSub(name) {{
  await fetch('/sources/reddit/remove?name=' + encodeURIComponent(name), {{method: 'POST'}});
  location.reload();
}}
async function addPod() {{
  const url = document.getElementById('podurl').value.trim();
  const name = document.getElementById('podname').value.trim();
  if (!url) return;
  await fetch('/sources/podcast/add?url=' + encodeURIComponent(url) +
              '&name=' + encodeURIComponent(name), {{method: 'POST'}});
  location.reload();
}}
async function rmPod(url) {{
  await fetch('/sources/podcast/remove?url=' + encodeURIComponent(url), {{method: 'POST'}});
  location.reload();
}}
</script>
</body></html>"""


def _render_status_page() -> str:
    """Pipeline health at a glance: corpus, index lag, services, connectors."""
    import sqlite3
    from datetime import datetime, timezone

    def ro(path):
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)

    chan_meta_gap = -1
    try:
        conn = ro(Path("P:/.data/yt-is/batch_status.sqlite"))
        complete = conn.execute(
            "SELECT COUNT(*) FROM analysis_status WHERE status='complete'"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM analysis_status WHERE status='pending'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM analysis_status WHERE status='failed'"
        ).fetchone()[0]
        channels = conn.execute(
            "SELECT COUNT(*) FROM channel_metadata").fetchone()[0]
        titleless = conn.execute(
            "SELECT COUNT(*) FROM analysis_status "
            "WHERE title IS NULL OR title=''").fetchone()[0]
        chan_meta_gap = conn.execute(
            "SELECT COUNT(*) FROM channel_metadata "
            "WHERE (thumbnail_url IS NULL OR thumbnail_url='') "
            "   OR (description IS NULL OR description='')").fetchone()[0]
        conn.close()
    except Exception:
        complete = pending = failed = channels = titleless = chan_meta_gap = -1

    # connector document counts
    conn = ro(Path("P:/.data/yt-is/transcripts.sqlite"))
    try:
        by_source = dict(conn.execute(
            "SELECT source, COUNT(*) FROM transcript_cache "
            "WHERE source IN ('reddit','hackernews','discord','rss') "
            "GROUP BY source").fetchall())
    finally:
        conn.close()

    # EF index state
    import json as _json
    ef_state = {}
    try:
        ef_state = _json.loads(
            Path("P:/.data/yt-is/ef/state.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    indexed_wm = ef_state.get("indexed_watermark", "")

    def since_hours(ts: str) -> str:
        if not ts:
            return "n/a"
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:  # watermark stamps are UTC but naive
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            return f"{age:.1f}h behind"
        except ValueError:
            return "n/a"

    # indexer daemon liveness
    indexer_alive = False
    try:
        pid = int(Path("P:/.data/yt-is/ef/incremental-service.pid")
                  .read_text().strip())
        out = __import__("subprocess").run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid}).ProcessName"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        indexer_alive = out in ("python", "pythonw")
    except Exception:
        pass

    qdrant_points = -1
    try:
        qc = server.client()
        from . import projection_server as _ps
        from . import buildspec as _bs
        qdrant_points = qc.count(
            _ps.collection_name(_bs.load_spec()["generation"]),
            exact=True).count
    except Exception:
        pass

    def num(v):
        return f"{v:,}" if isinstance(v, int) and v >= 0 else "?"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ytis — Status</title>
<meta http-equiv="refresh" content="60">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }}
  h1, h2 {{ color: #58a6ff; }} h2 {{ border-bottom: 1px solid #30363d; padding-bottom: .4rem; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  table {{ border-collapse: collapse; width: 100%; margin: .8rem 0; }}
  th, td {{ text-align: left; padding: .45rem .7rem; border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; }} td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .dim {{ color: #8b949e; }}
  .ok {{ color: #3fb950; }} .warn {{ color: #d29922; }} .bad {{ color: #f85149; }}
  .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: .9rem 1.3rem; }}
  .card .v {{ font-size: 1.5rem; font-weight: 700; color: #58a6ff; }}
</style></head><body>
<nav><a href="/">&larr; Search</a> · <a href="/home">Home</a> · <a href="/digest">Daily brief</a> ·
<a href="/review">YouTube channels</a> · <a href="/reddit">Reddit</a> ·
<a href="/discord">Discord</a> · <a href="/status">Status</a></nav>
<h1>Status</h1>
<p class="dim">{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC — refreshes every 60s</p>

<div class="cards">
  <div class="card"><div class="v">{num(complete)}</div>transcripts complete</div>
  <div class="card"><div class="v">{num(pending)}</div>pending</div>
  <div class="card"><div class="v">{num(qdrant_points)}</div>search chunks</div>
  <div class="card"><div class="v">{num(channels)}</div>channels</div>
</div>

<h2>Pipeline</h2>
<table>
<tr><td>Index watermark</td><td class="num">{indexed_wm or 'n/a'}</td>
    <td class="dim">{since_hours(indexed_wm)}</td></tr>
<tr><td>Indexer daemon</td><td class="num">{'<span class="ok">running</span>' if indexer_alive else '<span class="bad">down</span>'}</td><td></td></tr>
<tr><td>Warm query service</td><td class="num"><span class="ok">running</span></td><td></td></tr>
<tr><td>Failed transcripts</td><td class="num">{num(failed)}</td><td class="dim">~5-7% is normal</td></tr>
<tr><td>Missing titles</td><td class="num">{num(titleless)}</td><td class="dim">heals daily at 06:00</td></tr>
<tr><td>Channels missing metadata</td><td class="num">{num(chan_meta_gap)}</td><td class="dim">thumbnail/description; heals daily at 06:00</td></tr>
<tr><td>Scheduled</td><td class="num">05:00 / 06:00</td><td class="dim">index keeper / full content sync</td></tr>
</table>

<h2>Connector content (documents)</h2>
<table>
<tr><th>Source</th><th>Docs</th></tr>
<tr><td>Reddit</td><td class="num">{by_source.get('reddit', 0):,}</td></tr>
<tr><td>Hacker News</td><td class="num">{by_source.get('hackernews', 0):,}</td></tr>
<tr><td>RSS</td><td class="num">{by_source.get('rss', 0):,}</td></tr>
<tr><td>Discord</td><td class="num">{by_source.get('discord', 0):,}</td></tr>
</table>
</body></html>"""


def _render_source_page(kind: str) -> str:
    """Status page for a connector source: what's tracked, sync state.
    Read-only for now; management actions come with the /sources work."""
    import sqlite3
    import html as _html
    esc = _html.escape

    if kind == "reddit":
        conn = sqlite3.connect(
            f"file:{Path('P:/.data/yt-is/batch_status.sqlite')}?mode=ro",
            uri=True, timeout=10.0)
        try:
            subs = conn.execute("""
                SELECT subreddit, added_at FROM reddit_subreddits
                ORDER BY added_at""").fetchall()
        finally:
            conn.close()
        tdb = sqlite3.connect(
            f"file:{Path('P:/.data/yt-is/transcripts.sqlite')}?mode=ro",
            uri=True, timeout=10.0)
        try:
            counts = dict(tdb.execute("""
                SELECT json_extract(metadata_json, '$.subreddit'), COUNT(*)
                FROM transcript_cache WHERE source = 'reddit' GROUP BY 1
            """).fetchall())
            latest = tdb.execute("""
                SELECT MAX(cached_at) FROM transcript_cache
                WHERE source = 'reddit'""").fetchone()[0]
        finally:
            tdb.close()
        rows = "".join(
            f'<tr><td>r/{esc(s)}</td><td class="num">{counts.get(s, 0):,}</td></tr>'
            for s, _ in subs)
        title, sub = "Reddit sources", "tracked subreddits"
        extra = (f'<p class="dim">Latest sync: {esc(latest or "never")} · '
                 f'Manage via CLI: <code>ytis reddit</code></p>')

    elif kind == "discord":
        conn = sqlite3.connect(
            f"file:{Path('P:/.data/yt-is/batch_status.sqlite')}?mode=ro",
            uri=True, timeout=10.0)
        try:
            chans = conn.execute("""
                SELECT channel_name, guild_name, channel_id,
                       last_synced, total_batches
                FROM discord_channels ORDER BY guild_name, channel_name
            """).fetchall()
        finally:
            conn.close()
        rows = "".join(
            f'<tr><td>#{esc(c or "?")}</td><td>{esc(g or "?")}</td>'
            f'<td class="num">{b or 0:,}</td><td class="dim">{esc(ls or "never")[:19]}</td></tr>'
            for c, g, _cid, ls, b in chans) or (
            '<tr><td colspan="4" class="dim">Nothing tracked yet — and the bot '
            "isn't in any server. Once a server admin approves the bot invite "
            '(or a Discord History Tracker archive is ingested), channels '
            'appear here.</td></tr>')
        title, sub = "Discord sources", "tracked channels"
        extra = ('<p class="dim">Manage via CLI: '
                 '<code>python scripts/run_discord_sync.py --add &lt;channel_id&gt;</code> · '
                 '<code>ytis discord</code></p>')
    else:
        raise ValueError(f"unknown source kind {kind}")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ytis — {title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }}
  h1 {{ color: #58a6ff; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  table {{ border-collapse: collapse; width: 100%; margin: .8rem 0; }}
  th, td {{ text-align: left; padding: .45rem .7rem; border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .dim {{ color: #8b949e; }}
  code {{ background: #161b22; border: 1px solid #30363d; border-radius: 4px; padding: .1rem .4rem; }}
</style></head><body>
<nav><a href="/">&larr; Search</a> · <a href="/home">Home</a> · <a href="/digest">Daily brief</a> ·
<a href="/review">YouTube channels</a> · <a href="/reddit">Reddit</a> ·
<a href="/discord">Discord</a></nav>
<h1>{title}</h1>
<p class="dim">{len(rows.split('<tr>')) - 1 if rows else 0} {sub}</p>
<table><tr><th>{'Subreddit' if kind == 'reddit' else 'Channel'}</th>
{'<th>Server</th>' if kind == 'discord' else ''}
<th>{'Posts stored' if kind == 'reddit' else 'Batches'}</th>
{'<th>Last sync</th>' if kind == 'discord' else ''}</tr>{rows}</table>
{extra}
</body></html>"""


def _daily_counts(days: int = 7) -> list[dict]:
    """Per-day transcript/channel counts for the rolling window."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(
        f"file:{Path('P:/.data/yt-is/batch_status.sqlite')}?mode=ro", uri=True,
        timeout=10.0)
    try:
        rows = conn.execute("""
            SELECT substr(updated_at, 1, 10) AS day, COUNT(*),
                   COUNT(DISTINCT source)
            FROM analysis_status
            WHERE status = 'complete' AND updated_at >= ?
              AND source NOT LIKE '%://reddit.com%'
              AND source NOT LIKE '%://news.ycombinator.com%'
              AND source NOT LIKE '%://discord.com%'
            GROUP BY day ORDER BY day DESC
        """, (cutoff,)).fetchall()
    finally:
        conn.close()
    return [{"day": d, "transcripts": n, "channels": c} for d, n, c in rows]


def _render_digest_page() -> str:
    """The daily brief + 7-day rolling view, computed live at request time."""
    import html as _html
    import sqlite3
    import sys as _sys
    from datetime import datetime, timedelta, timezone

    _sys.path.insert(0, str(REPO / "scripts"))
    import generate_digest as gd

    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()

    # today (24h) and the rolling week, from the digest's own data functions
    today = gd.get_new_transcripts(24)
    week = gd.get_new_transcripts(168)
    reddit_week = gd.get_new_reddit(168)
    artifacts_week = gd.get_new_artifacts(168)
    stats = gd.get_stats()
    daily = _daily_counts(7)

    # per-day reddit counts
    conn = sqlite3.connect(
        f"file:{Path('P:/.data/yt-is/transcripts.sqlite')}?mode=ro", uri=True,
        timeout=10.0)
    try:
        reddit_daily = dict(conn.execute("""
            SELECT substr(cached_at, 1, 10), COUNT(*) FROM transcript_cache
            WHERE source = 'reddit' AND cached_at >= ? GROUP BY 1
        """, (week_ago,)).fetchall())
    finally:
        conn.close()

    esc = _html.escape

    def channel_rows(channels, limit):
        out = []
        for ch in channels[:limit]:
            titles = " · ".join(
                f'<a href="{esc(v["url"])}">{esc(v["title"][:90])}</a>'
                for v in ch["latest"])
            out.append(
                f'<tr><td>{esc(ch["name"][:60])}</td>'
                f'<td class="num">{ch["count"]:,}</td>'
                f'<td>{titles}</td></tr>')
        return "".join(out)

    day_rows = "".join(
        f'<tr><td>{d["day"]}</td><td class="num">{d["transcripts"]:,}</td>'
        f'<td class="num">{d["channels"]}</td>'
        f'<td class="num">{reddit_daily.get(d["day"], 0)}</td></tr>'
        for d in daily)

    top_reddit = sorted(reddit_week, key=lambda p: -(p.get("score") or 0))[:10]
    reddit_rows = "".join(
        f'<li><a href="{esc(p["url"])}">{esc(p["title"][:110])}</a> '
        f'<span class="dim">(r/{esc(p["subreddit"])}, {p.get("score", 0)} pts)</span></li>'
        for p in top_reddit)

    artifact_rows = "".join(
        f'<li><a href="{esc(a["url"])}">{esc(a["title"][:100])}</a> '
        f'<span class="dim">({a["size_kb"]}KB)</span></li>'
        for a in artifacts_week[:10])

    week_total = week["total"]
    today_total = today["total"]
    top_week_channels = channel_rows(week["channels"], 10)
    top_today_channels = channel_rows(today["channels"], 8)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>ytis — Daily Brief</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0d1117; color: #e6edf3; margin: 0; padding: 2rem; }}
  h1 {{ color: #58a6ff; }}
  h2 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: .4rem; }}
  a {{ color: #58a6ff; text-decoration: none; }} a:hover {{ text-decoration: underline; }}
  table {{ border-collapse: collapse; width: 100%; margin: .8rem 0; }}
  th, td {{ text-align: left; padding: .45rem .7rem; border-bottom: 1px solid #21262d; }}
  th {{ color: #8b949e; font-weight: 600; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .dim {{ color: #8b949e; }}
  .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
          padding: .9rem 1.3rem; }}
  .card .v {{ font-size: 1.6rem; font-weight: 700; color: #58a6ff; }}
  nav {{ margin-bottom: 1.5rem; }}
</style></head><body>
<nav><a href="/">&larr; Search</a> · <a href="/home">Home</a> · <a href="/review">YouTube channels</a> ·
<a href="/reddit">Reddit</a> · <a href="/discord">Discord</a> · <a href="/status">Status</a></nav>
<h1>Daily Brief</h1>
<p class="dim">{now.strftime('%A, %Y-%m-%d %H:%M')} UTC — computed live</p>

<div class="cards">
  <div class="card"><div class="v">{_channel_side_published(1):,}</div>new on channels (24h)</div>
  <div class="card"><div class="v">{_channel_side_published(7):,}</div>new on channels (7d)</div>
  <div class="card"><div class="v">{len(today['channels'])}</div>channels active</div>
  <div class="card"><div class="v">{len(reddit_week)}</div>Reddit posts (7d)</div>
  <div class="card"><div class="v">{len(artifacts_week)}</div>code artifacts (7d)</div>
</div>

<h2>Today — busiest channels</h2>
<table><tr><th>Channel</th><th>New</th><th>Latest</th></tr>{top_today_channels}</table>

<h2>Last 7 days</h2>
<table><tr><th>Day (UTC)</th><th>Transcripts</th><th>Channels</th><th>Reddit</th></tr>{day_rows}</table>

<h2>Busiest channels (7 days)</h2>
<table><tr><th>Channel</th><th>New</th><th>Latest</th></tr>{top_week_channels}</table>

<h2>Top Reddit (7 days, by score)</h2>
<ul>{reddit_rows}</ul>

<h2>New code artifacts (7 days)</h2>
<ul>{artifact_rows}</ul>

<h2>System</h2>
<p class="dim">{stats['complete']:,} transcripts complete · {stats['channels']:,} channels ·
{stats['pending']:,} pending · {stats['failed']:,} failed</p>
</body></html>"""


def library_lookup(video_id: str, catalog_db=None) -> dict:
    """Read-only library membership: exact EU identity from the fabric
    catalog (eu_id = video_id:transcript). Presence and provenance only,
    never transcript content."""
    import sqlite3
    from ef.catalog import CATALOG_DB
    db = catalog_db or CATALOG_DB
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10.0)
    try:
        row = conn.execute(
            "select eu_id, source, char_length, captured_at from eu "
            "where eu_id = ?", (f"{video_id}:transcript",)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"video_id": video_id, "status": "not_found"}
    eu_id, source, char_length, captured_at = row
    return {
        "video_id": video_id,
        "status": "in_library",
        "eu_id": eu_id,
        "transcript_chars": char_length,
        "transcript_source": source,
        "cached_at": captured_at or None,
    }


def reopen_exact(eu_id: str, start_char: int, end_char: int,
                 catalog_db=None, transcripts_db=None):
    """Exact authoritative span reopen: catalog eu_id -> authority_ref ->
    transcript_cache substr. The returned text length is exactly
    end_char - start_char; anything short of that fails closed (None)."""
    import sqlite3
    from ef.authority import TRANSCRIPTS_DB
    from ef.catalog import CATALOG_DB
    cat = sqlite3.connect(
        f"file:{catalog_db or CATALOG_DB}?mode=ro", uri=True, timeout=10.0)
    try:
        row = cat.execute(
            "select authority_ref, video_id from eu where eu_id = ?",
            (eu_id,)).fetchone()
    finally:
        cat.close()
    if row is None:
        return None
    authority_ref, video_id = row
    ro = sqlite3.connect(
        f"file:{transcripts_db or TRANSCRIPTS_DB}?mode=ro", uri=True,
        timeout=10.0)
    try:
        span = ro.execute(
            "select substr(transcript, ?, ?) from transcript_cache "
            "where cache_key = ?",
            (start_char + 1, end_char - start_char, authority_ref)).fetchone()
    finally:
        ro.close()
    if span is None or span[0] is None or len(span[0]) != end_char - start_char:
        return None
    return {
        "eu_id": eu_id,
        "video_id": video_id,
        "start_char": start_char,
        "end_char": end_char,
        "text": span[0],
    }


def _chs_search(query: str, top_k: int = 3) -> list[dict]:
    """FTS search over CHS conversation history (grok + claude + codex +
    zcode sessions), federated into ytis results as source_type
    'conversation'."""
    import sqlite3
    conn = sqlite3.connect(
        "file:P:/.data/chs/chat_history.db?mode=ro", uri=True, timeout=10.0)
    try:
        safe = " ".join(
            chr(34) + w.replace(chr(34), chr(32)) + chr(34)
            for w in query.split()[:8])
        rows = conn.execute(
            """SELECT m.content, m.role, m.provider, s.session_key
               FROM messages_fts f
               JOIN messages m ON m.message_id = f.message_id
               JOIN sessions s ON s.id = m.session_id
               WHERE messages_fts MATCH ?
               ORDER BY bm25(messages_fts) LIMIT ?""", (safe, top_k)).fetchall()
    finally:
        conn.close()
    out = []
    for content, role, provider, session_key in rows:
        text = (content or "").replace(chr(10), " ")
        out.append({
            "chunk_id": f"chs:{session_key}:{role}",
            "video_id": session_key,
            "title": f"[conversation] {provider}: {role}",
            "snippet": text[:300],
            "score": 0.05,
            "retrieval_paths": ("conversation_fts",),
            "url": "",
            "source_type": "conversation",
        })
    return out


def is_running() -> bool:
    """Check if the warm query service is already running."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if is_running():
        print(f"warm query service already running on {HOST}:{PORT}")
        return 0

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"warm EF query service starting on {HOST}:{PORT}")
    print(f"  model loading in background... queries will wait until warm")

    def shutdown(signum, frame):
        print("\nshutting down")
        server.shutdown()
        PID_FILE.unlink(missing_ok=True)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Warm the model in a background thread so the server accepts
    # connections immediately (health returns 503 until warm)
    def warm():
        try:
            get_query()
            print("  model warm — ready for queries")
        except Exception as e:
            print(f"  model load failed: {e}")

    threading.Thread(target=warm, daemon=True).start()

    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
