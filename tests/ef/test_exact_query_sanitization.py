"""A" section 6: FTS5 query sanitization (embedded quotes, apostrophes)."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import routing  # noqa: E402


def test_plain_terms_quoted():
    assert routing.sanitize_fts_query("semiconductor risk") == \
        '"semiconductor" "risk"'


def test_embedded_double_quotes_stripped():
    # titles like 'Why "AI" plan' must not break FTS5 syntax
    out = routing.sanitize_fts_query('Why "AI" plan fails')
    assert out == '"Why" "AI" "plan" "fails"'


def test_apostrophe_inside_term_survives():
    out = routing.sanitize_fts_query("republic's decline")
    assert out == "\"republic's\" \"decline\""


def test_only_punctuation_yields_empty():
    assert routing.sanitize_fts_query('" " ""') == ""


def test_quoted_literal_unwrapped_before_matching():
    assert routing.sanitize_fts_query('"GR0000tn2"') == '"GR0000tn2"'
