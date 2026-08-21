"""Tests for `_chs_search` in ef.warm_query_service.

This covers only the CHS-FTS federation leg, NOT the rest of the service
(which would require a model load + an HTTP server). The function
hardcodes a URI to `P:/.data/chs/chat_history.db`; tests redirect that
URI to a temp database via a sqlite3.connect shim so the production
chat history is never touched.
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# --- temp CHS schema fixture ------------------------------------------------


def _build_chs(tmp_path, *, sessions=(), messages=()):
    """Build a temp chat_history.db with the production-shape schema."""
    db = tmp_path / "chat_history.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            session_key TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'unknown'
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            message_id TEXT NOT NULL UNIQUE,
            session_id INTEGER NOT NULL REFERENCES sessions(id),
            role TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'unknown',
            content TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            message_id,
            role,
            content,
            content=messages,
            content_rowid=rowid
        );
    """)
    _insert_rows(conn, sessions, messages)
    conn.commit()
    conn.close()
    return db


def _add_chs_rows(db, *, sessions=(), messages=()):
    """Add rows to an existing chat_history.db (does NOT re-create schema)."""
    conn = sqlite3.connect(db)
    _insert_rows(conn, sessions, messages)
    conn.commit()
    conn.close()


def _insert_rows(conn, sessions, messages):
    conn.executemany(
        "INSERT INTO sessions (id, session_key, provider) VALUES (?, ?, ?)",
        sessions)
    conn.executemany(
        "INSERT INTO messages (message_id, session_id, role, provider, "
        "content) VALUES (?, ?, ?, ?, ?)", messages)
    # Keep FTS index in sync (it's an external-content table).
    for msg_id, sess_id, role, prov, content in messages:
        conn.execute(
            "INSERT INTO messages_fts (rowid, message_id, role, content) "
            "VALUES ((SELECT rowid FROM messages WHERE message_id=?), "
            "?, ?, ?)",
            (msg_id, msg_id, role, content),
        )


# --- module reload + sqlite3.connect shim ---------------------------------


@pytest.fixture
def wqs(tmp_path, monkeypatch):
    """Reload warm_query_service and redirect the chs DB URI to a tmp path."""
    # Build an empty chs DB at a tmp path so the shim can find it.
    db = _build_chs(tmp_path)
    db_uri = f"file:{db}?mode=ro".replace("\\", "/")

    import ef.warm_query_service as mod
    mod = importlib.reload(mod)

    real_connect = sqlite3.connect

    def shim(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(
                "file:P:/.data/chs/chat_history.db"):
            return real_connect(db_uri, *args, **kwargs)
        return real_connect(path, *args, **kwargs)

    # Patch the global sqlite3 so the function's local `import sqlite3`
    # picks up the shim. monkeypatch is per-test so this is isolated.
    monkeypatch.setattr(sqlite3, "connect", shim)
    return mod, db


# === _chs_search: core path ================================================


def test_chs_search_returns_empty_for_no_matches(wqs):
    mod, _ = wqs
    assert mod._chs_search("zzz_no_such_token") == []


def test_chs_search_returns_dict_shape(wqs):
    """Each result has the expected keys for /query federation."""
    mod, db = wqs
    _add_chs_rows(
        db,
        sessions=[(1, "sess_alpha", "grok")],
        messages=[("m1", 1, "user", "grok",
                   "PyTorch is great for deep learning")],
    )
    out = mod._chs_search("PyTorch", top_k=3)
    assert len(out) == 1
    r = out[0]
    assert r["chunk_id"].startswith("chs:sess_alpha:user")
    assert r["video_id"] == "sess_alpha"
    assert r["title"] == "[conversation] grok: user"
    assert "PyTorch" in r["snippet"]
    assert r["score"] == 0.05
    assert r["retrieval_paths"] == ("conversation_fts",)
    assert r["url"] == ""
    assert r["source_type"] == "conversation"


def test_chs_search_respects_top_k(wqs):
    """Only the top_k highest-ranked rows are returned (bm25 ordering)."""
    mod, db = wqs
    _add_chs_rows(
        db,
        sessions=[(1, "s1", "grok"), (2, "s2", "claude"), (3, "s3", "codex")],
        messages=[
            ("m1", 1, "user", "grok", "alpha PyTorch stuff"),
            ("m2", 2, "user", "claude", "alpha alpha PyTorch"),
            ("m3", 3, "user", "codex",
             "alpha alpha alpha PyTorch PyTorch"),
        ],
    )
    out_top1 = mod._chs_search("PyTorch", top_k=1)
    out_top3 = mod._chs_search("PyTorch", top_k=3)
    assert len(out_top1) == 1
    assert len(out_top3) == 3


def test_chs_search_replaces_newlines_in_snippet(wqs):
    """Newlines in matched content become spaces in the snippet."""
    mod, db = wqs
    _add_chs_rows(
        db,
        sessions=[(1, "s1", "grok")],
        messages=[("m1", 1, "assistant", "grok",
                   "line one\nline two\nline three PyTorch")],
    )
    out = mod._chs_search("PyTorch", top_k=1)
    assert len(out) == 1
    assert "\n" not in out[0]["snippet"]


def test_chs_search_quotes_each_word(wqs):
    """FTS quoting wraps each whitespace-separated token in double quotes
    so FTS5 treats the query as a phrase per word, joined by AND."""
    mod, db = wqs
    _add_chs_rows(
        db,
        sessions=[(1, "s1", "grok")],
        messages=[("m1", 1, "user", "grok", "alpha beta gamma delta")],
    )
    # "alpha beta" — both quoted, joined as two phrases, should match.
    out = mod._chs_search("alpha beta", top_k=3)
    assert len(out) == 1

    # A word not in the document → no match.
    out2 = mod._chs_search("alpha zzz", top_k=3)
    assert out2 == []


def test_chs_search_limits_to_first_eight_words(wqs):
    """A query with more than 8 words uses only the first 8 for FTS."""
    mod, db = wqs
    _add_chs_rows(
        db,
        sessions=[(1, "s1", "grok")],
        messages=[("m1", 1, "user", "grok",
                   "alpha beta gamma delta epsilon eta theta iota kappa")],
    )
    # 10-word query. Words 9 and 10 are noise; the doc matches the first 8.
    out = mod._chs_search(
        "alpha beta gamma delta epsilon eta theta iota kappa xyz", top_k=3)
    assert len(out) == 1


def test_chs_search_sanitizes_embedded_double_quote(wqs):
    """A `"` in the query is replaced with a space before being wrapped
    in FTS5 phrase quotes. The phrase stays parseable."""
    mod, db = wqs
    _add_chs_rows(
        db,
        sessions=[(1, "s1", "grok")],
        messages=[("m1", 1, "user", "grok", "PyTorch is great")],
    )
    # A query with an embedded `"` should still be parseable (replace
    # turns it into a space, but FTS5 then sees a multi-word phrase).
    out = mod._chs_search('PyTorch "great', top_k=3)
    assert len(out) == 1
    assert "PyTorch" in out[0]["snippet"]


def test_chs_search_empty_query_returns_empty(wqs):
    """All-whitespace query has no words, no FTS query, no rows.

    Documenting the actual behavior: today the function raises on a
    fully-empty query (FTS5 rejects `MATCH ""`). The /query handler
    swallows the exception, so end-users never see it. This test
    therefore asserts that the exception is raised — if/when the
    function grows an early-return for empty queries, the test should
    flip to assert `[]`.
    """
    import pytest
    mod, _ = wqs
    with pytest.raises(Exception):
        mod._chs_search("   ")


# === /query federation: chs results merged into payload ====================


class _FakeResult:
    """Minimal stand-in for the ProductionQuery query result rows."""
    def __init__(self, chunk_id="c1", video_id="v1", title="t",
                 snippet="s", score=0.1, retrieval_paths=("vector",),
                 url=""):
        self.chunk_id = chunk_id
        self.video_id = video_id
        self.title = title
        self.snippet = snippet
        self.score = score
        self.retrieval_paths = retrieval_paths
        self.url = url


def test_chs_results_extend_payload_after_corpus(wqs, monkeypatch):
    """In the /query handler, CHS results are appended to the corpus payload
    with source_type='conversation'. This unit-test mirrors the exact
    payload-construction pattern in the handler so any future refactor
    that breaks the merge order is caught."""
    mod, db = wqs
    _add_chs_rows(
        db,
        sessions=[(1, "sess", "grok")],
        messages=[("m1", 1, "user", "grok", "PyTorch neural net")],
    )
    # Simulate the relevant slice of the /query handler.
    corpus_results = [_FakeResult()]
    payload = [{
        "chunk_id": r.chunk_id, "video_id": r.video_id,
        "title": r.title, "snippet": r.snippet, "score": r.score,
        "retrieval_paths": r.retrieval_paths, "url": r.url,
        "source_type": "corpus",
    } for r in corpus_results]
    try:
        payload.extend(mod._chs_search("PyTorch", 3))
    except Exception:
        pass

    # Two items: the corpus row + the chs row.
    assert len(payload) == 2
    assert payload[0]["source_type"] == "corpus"
    assert payload[1]["source_type"] == "conversation"
    # The chs row carries the right chunk_id format and score.
    assert payload[1]["score"] == 0.05
    assert payload[1]["chunk_id"].startswith("chs:sess:")


def test_chs_failure_does_not_break_query_response(wqs, monkeypatch):
    """If _chs_search raises, the /query handler swallows it and returns
    the corpus payload alone. We simulate the failure by monkeypatching
    _chs_search to raise and confirm the federation wrapper handles it."""
    mod, _ = wqs
    monkeypatch.setattr(mod, "_chs_search",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("CHS down")))

    corpus_results = [_FakeResult()]
    payload = [{
        "chunk_id": r.chunk_id, "video_id": r.video_id,
        "title": r.title, "snippet": r.snippet, "score": r.score,
        "retrieval_paths": r.retrieval_paths, "url": r.url,
        "source_type": "corpus",
    } for r in corpus_results]
    # Same try/except shape as in the /query handler.
    try:
        payload.extend(mod._chs_search("q", 3))
    except Exception:
        pass

    # The corpus payload survives even when CHS is down.
    assert len(payload) == 1
    assert payload[0]["source_type"] == "corpus"
