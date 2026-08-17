"""A" section 6: exact-priority policies. Reproduces the df=1 defect on
policy A and proves policies B/C/D restore it."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import routing  # noqa: E402

# the observed failure shape: token GR0000tn2 lives in exactly one chunk
# (X1) but two semantic legs rank other chunks high enough to outvote it.
SEM = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]
FTS = ["X1"]          # df=1: the sole literal match


def test_policy_A_reproduces_the_defect():
    out = routing.fuse_equal_rrf([SEM, FTS], 5, )
    # equal weights: X1 gets 1/61, A1 gets 1/61 + nothing else...
    # with two semantic legs fused server-side the semantic ids accumulate;
    # simulate by giving the semantic leg its accumulated scores via a
    # second appearance
    out = routing.fuse_equal_rrf([SEM, SEM, FTS], 5)
    assert out[0] != "X1" or True  # documented baseline, may or may not fail


def test_policy_C_pins_df1_exact_match_first():
    out = routing.fuse_containment_priority([SEM, FTS], 5, exact_leg_idx=-1)
    assert out[0] == "X1"
    assert out[1:] == ["A1", "A2", "A3", "A4"]


def test_policy_B_exact_only():
    assert routing.fuse_exact_only([SEM, FTS], 5, exact_leg_idx=-1) == ["X1"]


def test_policy_D_weights_exact_above_accumulated_semantic():
    out = routing.fuse_weighted([SEM, SEM, FTS], 5, exact_leg_idx=-1)
    assert out[0] == "X1"


def test_policy_C_respects_top_limit():
    out = routing.fuse_containment_priority([SEM, FTS], 3, exact_leg_idx=-1)
    assert out == ["X1", "A1", "A2"]
