"""Design-invariant tests for the DHT vision quality gate.

The 73/732 soft-failure regression (2026-08-21) was caused by the
`flag` substring meta-marker in gemini_extract._agy_output_is_task,
which false-positived on chart-strategy names like "Bull Iron Flag",
"Bull Flag", "Bear Flag", etc. The fix landed in
extract_dht_artifacts._dht_vision_quality_gate (commit af80b6e6).

These tests lock in the gate's behavior so the same bug can't
regress. They run as a normal pytest; if any of these assertions
fire, the gate is too strict (or the prompt is wrong) and 10% of
chart artifacts will be marked soft-failure again.

Run: pytest tests/test_extract_dht_quality_gate.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.extract_dht_artifacts import _dht_vision_quality_gate


# --- the regression that started this whole story ---
def test_bull_iron_flag_passes_gate():
    """The literal response that was rejected by the old gate. Must pass."""
    text = (
        "PART 1 — VISIBLE TEXT AND NUMBERS:\n"
        "Bull Iron Flag contracts strike expiration enter price exit price P/L\n"
        "SPX INDEX calls 3 (buy) 3685 30-Dec-20 7-Dec-20 66.36 21-Dec-20 36.34 -$9,006\n"
        "SPX INDEX calls -3 (sell) 3675 30-Dec-20 7-Dec-20 72.67 21-Dec-20 42.03 $9,192\n"
        "SPX INDEX puts -3 (sell) 3565 28-Dec-20 7-Dec-20 23.39 21-Dec-20 15.21 $2,454\n"
        "SPX INDEX puts 3 (buy) 3510 28-Dec-20 7-Dec-20 16.55 21-Dec-20 9.10 -$2,235\n"
        "credit = 13.15 debit = 11.80 $405\n\n"
        "PART 2 — VISUAL MEANING AND CONTEXT:\n"
        "The image is a table detailing an options strategy identified as a "
        "\"Bull Iron Flag\". It lists four legs of the strategy, two calls and two puts, "
        "all on SPX INDEX."
    )
    assert _dht_vision_quality_gate(text), \
        "Bull Iron Flag response must pass the gate (regression: was rejected by old gate)"


def test_bull_flag_bear_flag_passes():
    """Strategy names containing 'flag' must not be false-positives."""
    for name in ("Bull Flag", "Bear Flag", "Reverse Butterfly", "Iron Butterfly"):
        text = (
            f"PART 1 — VISIBLE TEXT AND NUMBERS:\n"
            f"This image shows a {name} on SPX INDEX. The strike is 4000 "
            f"with expiration 30-Dec-20. The trade has 4 legs: two calls and "
            f"two puts. Entry price 2.50, exit 1.20, P/L -$1,300. "
            f"Credit collected 1.30, debit paid 2.50. Net loss 1.20 per contract.\n\n"
            f"PART 2 — VISUAL MEANING AND CONTEXT:\n"
            f"The image is an options strategy identified as a {name}. "
            f"It consists of four legs, two calls and two puts, on SPX INDEX, "
            f"with a strike of 4000 expiring on 30-Dec-20."
        )
        assert _dht_vision_quality_gate(text), f"{name} response must pass"


def test_real_meta_text_rejected():
    """agy meta-text (CLI discussion) must be rejected."""
    text = (
        "To set up the antigravity CLI, you'll need to pass "
        "--print-timeout and --dangerously-skip flags. The pytest test "
        "runner uses argparse to parse the click arguments."
    )
    assert not _dht_vision_quality_gate(text), "agy's antigravity-CLI meta-text must be rejected"


def test_lazy_preamble_rejected():
    """Lazy LLM preambles must be rejected."""
    assert not _dht_vision_quality_gate(
        "Certainly! I'd be happy to help you with that. Here is the analysis: 9 8 7 6 5"
    )
    assert not _dht_vision_quality_gate(
        "Here is the description you requested: 12 34 56"
    )


def test_empty_short_nodigit_rejected():
    """Empty/short/no-digit responses must be rejected."""
    assert not _dht_vision_quality_gate("")
    assert not _dht_vision_quality_gate("a")
    assert not _dht_vision_quality_gate("a" * 300)  # 300 chars, no digits
    assert not _dht_vision_quality_gate("a" * 100)  # short


def test_error_wrapper_rejected():
    """Canonical 'Error:' wrappers must be rejected."""
    text_with_error = "Error: something went wrong. " + "9" * 200
    assert not _dht_vision_quality_gate(text_with_error)


def test_real_chart_description_passes():
    """A normal chart description (no meta-text) should pass."""
    text = (
        "PART 1 — VISIBLE TEXT AND NUMBERS:\n"
        "Date: 2/15/2023. Market Model: 5.30. AD Line: outperforming. "
        "VIX: 18.2. 7 Day MA: up. 200 Day MA: above. "
        "Support & Res: 3900 to 4200. SUMMARY: BULLISH +2.\n\n"
        "PART 2 — VISUAL MEANING AND CONTEXT:\n"
        "A market analysis dashboard dated February 15, 2023, with "
        "indicators VIX, AD Line, and moving averages."
    )
    assert _dht_vision_quality_gate(text)
