from __future__ import annotations

from scripts.check_ef_warm_service_deps import _evaluate


def _probe(*, versions=None, failed=()):
    versions = versions or {}
    return {
        name: {"version": versions.get(name, "1"), "import": name not in failed}
        for name in set(versions) | set(failed)
    }


def test_evaluate_accepts_matching_machine_and_service_contract():
    expected = {"torch": "2", "mcp": "1.26"}
    machine = _probe(versions=expected)
    service = _probe(versions=expected)
    assert _evaluate(machine, service, expected) == []


def test_evaluate_does_not_block_on_machine_only_encoder_gap():
    expected = {"torch": "2", "mcp": "1.26"}
    machine = _probe(versions={"mcp": "1.26"}, failed=("torch",))
    service = _probe(versions=expected)
    assert _evaluate(machine, service, expected) == []


def test_evaluate_rejects_service_version_drift():
    expected = {"torch": "2", "mcp": "1.26"}
    machine = _probe(versions=expected)
    service = _probe(versions={"torch": "2", "mcp": "1.5"})
    reasons = _evaluate(machine, service, expected)
    assert any("service mcp version '1.5'" in reason for reason in reasons)
