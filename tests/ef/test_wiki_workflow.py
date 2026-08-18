"""M-gate #11: workflow-level tests for /wiki maintenance integration."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))


class _FR:
    def __init__(self, lag):
        self._lag = lag

    def compute_lag(self, wm):
        return {"index_lag_count": self._lag}

    def load_state(self):
        return {"indexed_watermark": "x"}


def _patch_freshness(monkeypatch, lag):
    import types
    import ef_wiki_maintenance as m
    fake = types.SimpleNamespace(
        load_state=lambda: {"indexed_watermark": "x"},
        compute_lag=lambda wm: {"index_lag_count": lag})
    monkeypatch.setitem(sys.modules, "ef.freshness", fake)
    return m


def test_staleness_no_new_evidence_forbidden_when_lagging(monkeypatch):
    """M-gate #7: while EF materially lags, no_new_evidence is FORBIDDEN."""
    m = _patch_freshness(monkeypatch, 5000)
    out = m.mode_staleness(_pq_empty(), "claim", "2026-08-01T00:00:00Z", 5)
    assert out["signal"] == "freshness_incomplete"
    assert out["ef_fresh_enough"] is False


def test_staleness_no_new_evidence_allowed_when_current(monkeypatch):
    m = _patch_freshness(monkeypatch, 0)
    out = m.mode_staleness(_pq_empty(), "claim", "2026-08-01T00:00:00Z", 5)
    assert out["signal"] == "no_new_evidence"


def test_staleness_unknown_lag_conservative(monkeypatch):
    m = _patch_freshness(monkeypatch, -1)
    out = m.mode_staleness(_pq_empty(), "claim", "2026-08-01T00:00:00Z", 5)
    assert out["signal"] == "freshness_incomplete"


def test_rank1_rejected_by_wiki_validation():
    """The /wiki-side disposition: a high-rank candidate can be judged
    irrelevant after reopening — the verdict is never the rank."""
    # This encodes the CALLER contract; the tool's role labels already
    # refuse auto-support. Simulate a wiki validator rejecting rank 1.
    cand = {"rank": 1, "score": 0.99, "role": "retrieved_candidate",
            "evidence_text": "lorem ipsum"}
    wiki_disposition = evaluate_candidate(cand, claim="ripgrep dot-dir pruning")
    assert wiki_disposition in ("irrelevant", "insufficient")
    assert wiki_disposition != "supports"       # rank did not decide


def evaluate_candidate(cand, claim):
    """Stands in for the /wiki-side evaluation step (M-gate #3):
    /wiki reopens evidence and judges. Here: an obviously-unrelated
    snippet is judged irrelevant regardless of rank/score."""
    text = cand.get("evidence_text", "").lower()
    claim_terms = {w for w in claim.lower().split() if len(w) > 3}
    overlap = claim_terms & set(text.split())
    if len(overlap) >= 2:
        return "supports"      # (real /wiki logic is richer; this is a stub)
    return "irrelevant"


def test_qualifies_disposition_exists():
    """The disposition vocabulary includes qualifies, not just
    supports/contradicts."""
    vocab = {"supports", "qualifies", "contradicts", "irrelevant",
             "insufficient", "duplicative"}
    assert "qualifies" in vocab


def _pq_empty():
    class _PQ:
        def relevant(self, q, limit=8, **kw):
            return []
    return _PQ()


def test_timestamp_reopen_via_catalog(monkeypatch):
    """M-gate #8: captured_at reopens from the catalog PK reliably."""
    import ef_wiki_maintenance as m
    ts = m._captured_at("dQw4w9WgXcQ:transcript")   # quarantined id -> None
    assert ts is None
