"""A" sections 6-7 tests: intent detection separates exact identifiers,
natural language, and common lexical terms. Length NEVER implies intent."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import routing  # noqa: E402

EXACT_SHAPES = [
    "GR0000tn2", "hizoJc", "RPC9", "--resume-worker", "Qwen3-Reranker-4B",
    "ClassName.method_name", "ERROR_RESOURCE_EXHAUSTED", "BF-16",
    "tabverified.substack.com", "check_default_procs", "OpenAI",
    "0xdeadbeef",
]
NATURAL = [
    "cook rice", "market bottom", "agent architecture",
    "how does source age affect throughput", "why neural networks generalize",
]
COMMON_LEXICAL = ["YouTube", "Google", "Python"]


@pytest.mark.parametrize("q", EXACT_SHAPES)
def test_identifier_shaped_exact_forms(q):
    assert routing.identifier_shaped(q), q


@pytest.mark.parametrize("q", NATURAL + COMMON_LEXICAL)
def test_natural_not_identifier_shaped(q):
    # single common words ARE identifier-shaped; the df test separates them
    if " " in q:
        assert not routing.identifier_shaped(q), q


def test_short_natural_query_stays_semantic_even_with_df():
    # "cook rice" is two words: NOT an identifier, semantic regardless of df
    r = routing.classify("cook rice", df=1)
    assert r.intent == "semantic"
    r2 = routing.classify("agent architecture", df=0)
    assert r2.intent == "semantic"


def test_rare_identifier_routes_exact():
    r = routing.classify("GR0000tn2", df=1)
    assert r.intent == "exact"


def test_common_identifier_word_routes_semantic():
    r = routing.classify("YouTube", df=17287)
    assert r.intent == "semantic"
    assert "common" in r.reason


def test_explicit_exact_mode_forces_exact():
    assert routing.classify("market bottom", exact=True).intent == "exact"
    assert routing.classify("YouTube", exact=True).intent == "exact"


def test_quoted_literal_forces_exact():
    assert routing.classify('"market bottom"').intent == "exact"
    assert routing.classify("'GR0000tn2'").intent == "exact"


def test_sentence_containing_identifier_is_semantic():
    # containment is not intent: a question about an identifier is semantic
    r = routing.classify("how do I use RPC9 in practice")
    assert r.intent == "semantic"


def test_moderate_df_identifier_boundary():
    assert routing.classify("hizoJc", df=100).intent == "exact"
    assert routing.classify("hizoJc", df=101).intent == "semantic"
