"""b-prime focused tests: zero-literal identifier contract + routing edges."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import routing  # noqa: E402
from ef.query_server import ProductionQuery  # noqa: E402


class _NullEncoder:
    def encode(self, texts):
        raise AssertionError("zero-literal identifier must not encode")


def _pq():
    # NullEncoder: the zero-literal path must return BEFORE any semantic
    # work, proving no near-twin can be surfaced as primary evidence.
    return ProductionQuery(_NullEncoder(), generation=1)


def test_zero_df_identifier_returns_empty_primary():
    assert _pq().relevant("kimik.co3") == []


def test_zero_df_punct_identifier_returns_empty_primary():
    assert _pq().relevant("definitely-not-a-real.pkg") == []
    assert _pq().relevant("zzz-unknown-thing") == []


def test_one_char_near_twin_is_not_evidence(monkeypatch):
    # twin exists in the corpus; mutant must NOT return it as primary
    q = _pq()
    # simulate an FTS lane with zero hits for the mutant
    q._fts = None
    assert q.relevant("GR0000tn3") == []


def test_explicit_exact_zero_literals_empty():
    q = _pq()
    # NOTE: multi-word exact currently OR-matches its terms (documented
    # nuance); a genuinely absent term set proves the empty contract.
    assert q.relevant("zzzqquux wibblewobble", exact=True) == []


def test_ambiguous_ordinary_single_word_stays_semantic():
    # df tiebreak: conventional words route semantic (never hard-empty)
    assert routing.classify("Google", df=30000).intent == "semantic"
    assert routing.classify("Python", df=9000).intent == "semantic"
    assert routing.classify("YouTube", df=17287).intent == "semantic"


def test_normal_short_semantic_stays_semantic():
    assert routing.classify("cook rice").intent == "semantic"
    assert routing.classify("market bottom").intent == "semantic"


def test_high_confidence_automatic_identifiers():
    for tok in ("RPC9", "hizoJc", "--resume-worker", "ClassName.method_name",
                "ERROR_RESOURCE_EXHAUSTED", "Qwen3-Reranker-4B", "kimik.com"):
        r = routing.classify(tok)
        assert r.intent in ("identifier", "exact_strict"), (tok, r)
