"""E-gate P0 tests: evaluators can NEVER promote; promote fails closed."""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from ef import buildspec, receipt  # noqa: E402


def _wr(tmp_path, suite, gates, auth=False, req=None):
    return receipt.write_receipt(suite, gates, True,
                                  promotion_authorized=auth,
                                  required_gates=req,
                                  out_dir=tmp_path)


def test_regression_suites_not_promotion_authorized():
    for suite in ("c1_final_replay", "c2_final_battery", "c3_final_battery"):
        assert suite not in receipt.PROMOTION_AUTHORIZED_SUITES


def test_write_receipt_never_touches_active_generation(tmp_path):
    before = buildspec.active_generation()
    gates = {"everything": {"pass": True}}
    out = receipt.write_receipt("c1_final_replay", gates, True,
                                promotion_authorized=False)
    assert out.exists()
    assert buildspec.active_generation() == before   # unchanged


def test_receipts_are_immutable(tmp_path):
    receipt.write_receipt("c2_final_battery", {"g": {"pass": True}}, True,
                          out_dir=tmp_path)
    with pytest.raises(FileExistsError):
        receipt.write_receipt("c2_final_battery", {"g": {"pass": True}}, True,
                              out_dir=tmp_path)


def test_promote_refuses_non_authorized_suite(tmp_path):
    out = _wr(tmp_path, "c1_final_replay", {"g": {"pass": True}},
              auth=True)   # even if it CLAIMS authorization
    with pytest.raises(ValueError, match="not promotion-authorized"):
        receipt.promote_from_receipt(out)


def test_promote_refuses_fail_receipt(tmp_path):
    out = receipt.write_receipt("c4_final_battery", {"g": {"pass": False}},
                                False, out_dir=tmp_path)
    with pytest.raises(ValueError):
        receipt.promote_from_receipt(out)


def test_promote_refuses_tampered_receipt(tmp_path):
    out = _wr(tmp_path, "c4_final_battery", {"g": {"pass": True}},
              auth=True)
    doc = json.loads(out.read_text(encoding="utf-8"))
    doc["payload"]["gates"]["g"]["pass"] = False   # tamper
    out.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        receipt.promote_from_receipt(out)


def test_promote_refuses_missing_required_gate(tmp_path):
    out = receipt.write_receipt("c4_final_battery",
                                {"g1": {"pass": True}},
                                True, promotion_authorized=True,
                                required_gates=["g1", "g2"],
                                out_dir=tmp_path)
    with pytest.raises(ValueError, match="g2"):
        receipt.promote_from_receipt(out)
