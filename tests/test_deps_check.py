"""Tests for csf/deps_check.py — startup dependency version check."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Ensure package importable when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csf import deps_check
from csf.deps_check import (
    DepCheckResult,
    DepStatus,
    check_dependencies,
    format_report,
    run_check,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_requirements(tmp_path: Path) -> Path:
    """Write a small fake requirements.txt for testing."""
    req = tmp_path / "requirements.txt"
    req.write_text(
        "\n".join(
            [
                "# comment line",
                "",
                "packaging>=20.0",  # always installed in test env
                "yt-dlp>=2024.0.0",
                "google-genai>=999.0.0",  # impossibly high -> outdated
                "nonexistent-fake-pkg-xyz>=1.0.0",  # -> missing
                "pyyaml # inline comment",  # unconstrained, has inline comment
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return req


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip env vars that would change deps_check behavior."""
    monkeypatch.delenv("YTIS_SKIP_DEPS_CHECK", raising=False)
    monkeypatch.delenv("YTIS_STRICT_DEPS", raising=False)


# ---------------------------------------------------------------------------
# check_dependencies
# ---------------------------------------------------------------------------


class TestCheckDependencies:
    def test_returns_dep_check_result(self, fake_requirements: Path) -> None:
        result = check_dependencies(requirements_path=fake_requirements)
        assert isinstance(result, DepCheckResult)

    def test_detects_outdated(self, fake_requirements: Path) -> None:
        result = check_dependencies(requirements_path=fake_requirements)
        names = [d.name for d in result.outdated]
        assert "google-genai" in names

    def test_detects_missing(self, fake_requirements: Path) -> None:
        result = check_dependencies(requirements_path=fake_requirements)
        names = [d.name for d in result.missing]
        assert "nonexistent-fake-pkg-xyz" in names

    def test_ok_packages_collected(self, fake_requirements: Path) -> None:
        result = check_dependencies(requirements_path=fake_requirements)
        names = [d.name for d in result.ok]
        assert "packaging" in names
        assert "yt-dlp" in names

    def test_inline_comment_stripped(self, fake_requirements: Path) -> None:
        """pyyaml line with inline comment should parse as pyyaml."""
        result = check_dependencies(requirements_path=fake_requirements)
        names_ok = [d.name for d in result.ok]
        names_outdated = [d.name for d in result.outdated]
        names_missing = [d.name for d in result.missing]
        # pyyaml should be classified somewhere, not in unparseable
        all_names = names_ok + names_outdated + names_missing
        assert "pyyaml" in all_names

    def test_has_problems_true_when_outdated(self, fake_requirements: Path) -> None:
        result = check_dependencies(requirements_path=fake_requirements)
        assert result.has_problems is True

    def test_has_problems_false_when_all_ok(self, tmp_path: Path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("packaging>=20.0\n", encoding="utf-8")
        result = check_dependencies(requirements_path=req)
        assert result.has_problems is False

    def test_missing_requirements_file(self, tmp_path: Path) -> None:
        result = check_dependencies(requirements_path=tmp_path / "nonexistent.txt")
        assert any("not found" in reason for _, reason in result.unparseable)

    def test_empty_requirements_file(self, tmp_path: Path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("", encoding="utf-8")
        result = check_dependencies(requirements_path=req)
        assert result.has_problems is False
        assert len(result.ok) == 0

    def test_marker_line_treated_unparseable(self, tmp_path: Path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text(
            'requests>=2.31.0; python_version<"3.9"\n', encoding="utf-8"
        )
        result = check_dependencies(requirements_path=req)
        assert any(
            "marker" in reason.lower() for _, reason in result.unparseable
        )

    def test_never_raises_on_bad_syntax(self, tmp_path: Path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text(
            "this is not valid PEP 508 === broken\n", encoding="utf-8"
        )
        # Must not raise
        result = check_dependencies(requirements_path=req)
        assert len(result.unparseable) >= 1

    def test_nlm_cli_probed_when_available(self, fake_requirements: Path) -> None:
        """When nlm is on PATH, version should be populated or error captured."""
        result = check_dependencies(requirements_path=fake_requirements)
        # Either a version or an error — one must be set
        assert result.nlm_cli_version is not None or result.nlm_cli_error is not None


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_all_ok_report(self) -> None:
        result = DepCheckResult(
            ok=[
                DepStatus(name="packaging", required=">=20.0", installed="26.0", ok=True)
            ]
        )
        lines = format_report(result)
        assert any("OK" in line for line in lines)
        assert not any("OUTDATED" in line for line in lines)

    def test_outdated_in_report(self) -> None:
        result = DepCheckResult(
            outdated=[
                DepStatus(
                    name="google-genai",
                    required=">=999.0.0",
                    installed="2.12.1",
                    ok=False,
                )
            ]
        )
        lines = format_report(result)
        joined = "\n".join(lines)
        assert "OUTDATED" in joined
        assert "google-genai" in joined
        assert "2.12.1" in joined

    def test_missing_in_report(self) -> None:
        result = DepCheckResult(
            missing=[
                DepStatus(
                    name="ghost-pkg",
                    required=">=1.0.0",
                    installed=None,
                    ok=False,
                )
            ]
        )
        lines = format_report(result)
        joined = "\n".join(lines)
        assert "MISSING" in joined
        assert "ghost-pkg" in joined

    def test_hint_lines_present_when_problems(self) -> None:
        result = DepCheckResult(
            outdated=[
                DepStatus(
                    name="x", required=">=1.0", installed="0.5", ok=False
                )
            ]
        )
        lines = format_report(result)
        joined = "\n".join(lines)
        assert "YTIS_STRICT_DEPS=1" in joined
        assert "YTIS_SKIP_DEPS_CHECK=1" in joined


# ---------------------------------------------------------------------------
# run_check
# ---------------------------------------------------------------------------


class TestRunCheck:
    def test_returns_zero_when_ok(self, tmp_path: Path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("packaging>=20.0\n", encoding="utf-8")
        # Patch the module-level REQUIREMENTS_PATH so check_dependencies reads ours
        with mock.patch.object(deps_check, "REQUIREMENTS_PATH", req):
            code = run_check(strict=False, skip=False)
        assert code == 0

    def test_returns_zero_on_outdated_in_warn_mode(self, tmp_path: Path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("google-genai>=999.0.0\n", encoding="utf-8")
        with mock.patch.object(deps_check, "REQUIREMENTS_PATH", req):
            code = run_check(strict=False, skip=False)
        # Warn mode does not block
        assert code == 0

    def test_returns_one_on_outdated_in_strict_mode(self, tmp_path: Path) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("google-genai>=999.0.0\n", encoding="utf-8")
        with mock.patch.object(deps_check, "REQUIREMENTS_PATH", req):
            code = run_check(strict=True, skip=False)
        assert code == 1

    def test_returns_two_when_skipped(self) -> None:
        code = run_check(strict=True, skip=True)
        assert code == 2

    def test_env_strict_var_respected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("google-genai>=999.0.0\n", encoding="utf-8")
        monkeypatch.setenv("YTIS_STRICT_DEPS", "1")
        with mock.patch.object(deps_check, "REQUIREMENTS_PATH", req):
            code = run_check()  # strict=None -> reads env
        assert code == 1

    def test_env_skip_var_respected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        req = tmp_path / "requirements.txt"
        req.write_text("google-genai>=999.0.0\n", encoding="utf-8")
        monkeypatch.setenv("YTIS_SKIP_DEPS_CHECK", "1")
        with mock.patch.object(deps_check, "REQUIREMENTS_PATH", req):
            code = run_check(strict=True)  # skip=None -> reads env
        assert code == 2

    def test_env_truthy_variants(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for val in ("1", "true", "TRUE", "yes", "on", "True"):
            monkeypatch.setenv("YTIS_SKIP_DEPS_CHECK", val)
            assert deps_check._env_truthy("YTIS_SKIP_DEPS_CHECK") is True
        for val in ("0", "", "false", "no", "off", "  "):
            monkeypatch.setenv("YTIS_SKIP_DEPS_CHECK", val)
            assert deps_check._env_truthy("YTIS_SKIP_DEPS_CHECK") is False


# ---------------------------------------------------------------------------
# Integration: parse real requirements.txt
# ---------------------------------------------------------------------------


class TestRealRequirements:
    """Smoke test against the actual requirements.txt in the repo."""

    def test_real_requirements_parses_cleanly(self) -> None:
        result = check_dependencies()  # uses default REQUIREMENTS_PATH
        # The real file should have at least some parseable entries
        total = len(result.ok) + len(result.outdated) + len(result.missing)
        assert total > 0
        # And no parse errors on the real file
        assert len(result.unparseable) == 0, (
            f"real requirements.txt has unparseable lines: {result.unparseable}"
        )

    def test_real_requirements_current_versions_pass(self) -> None:
        """After the 2026-07-20 upgrade, all real deps should satisfy minima."""
        result = check_dependencies()
        assert not result.outdated, (
            f"outdated packages detected: {[d.name for d in result.outdated]}"
        )
        assert not result.missing, (
            f"missing packages detected: {[d.name for d in result.missing]}"
        )
