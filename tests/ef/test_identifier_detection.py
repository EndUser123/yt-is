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


def test_short_natural_query_stays_semantic():
    assert routing.classify("cook rice").intent == "semantic"
    assert routing.classify("agent architecture").intent == "semantic"


def test_rare_identifier_routes_identifier():
    assert routing.classify("GR0000tn2").intent == "identifier"


def test_conventional_words_route_semantic_df_tiebreak():
    # ambiguous single-word class: df tiebreak per operator examples
    assert routing.classify("YouTube", df=17287).intent == "semantic"
    assert routing.classify("Google", df=30000).intent == "semantic"
    assert routing.classify("Python", df=9000).intent == "semantic"


def test_rare_single_word_routes_identifier():
    assert routing.classify("hizoJc", df=1).intent == "identifier"


def test_explicit_exact_mode_forces_exact_strict():
    assert routing.classify("market bottom", exact=True).intent == "exact_strict"
    assert routing.classify("YouTube", exact=True).intent == "exact_strict"


def test_quoted_literal_forces_exact_strict():
    assert routing.classify('"market bottom"').intent == "exact_strict"
    assert routing.classify("'GR0000tn2'").intent == "exact_strict"


def test_sentence_containing_identifier_is_semantic():
    # containment is not intent: a question about an identifier is semantic
    r = routing.classify("how do I use RPC9 in practice")
    assert r.intent == "semantic"


def test_strong_shapes_are_df_independent():
    # strong structure (digits/punct/snake/CLI) => identifier at ANY df
    for shaped in ("RPC9", "GR0000tn2", "--resume-worker",
                   "ClassName.method_name", "gsd-map-codebase", "2.1.156"):
        assert routing.classify(shaped, df=50000).intent == "identifier"


def test_identifier_priority_fusion_contract():
    lit = ["L1", "L2", "L3"]
    sem = ["S1", "L3", "S2", "L1"]
    out = routing.fuse_identifier_priority(lit, sem, 3)
    assert set(out) == set(lit)          # containment guaranteed
    assert out[0] == "L3"                # semantic rank orders literals
    out2 = routing.fuse_identifier_priority(["L1"], sem, 3)
    assert out2[0] == "L1"               # literal first
    assert "S1" in out2                  # semantic fill when short
