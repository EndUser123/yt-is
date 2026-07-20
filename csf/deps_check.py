"""Startup dependency version check.

Verifies installed Python packages meet the minimums declared in
``requirements.txt`` before yt-is begins work. Default behavior is to
warn to stderr and continue; set ``YTIS_STRICT_DEPS=1`` to exit nonzero
on any outdated or missing package. Set ``YTIS_SKIP_DEPS_CHECK=1`` to
silence the check entirely.

The check degrades gracefully: if the ``packaging`` library is missing,
``requirements.txt`` is unreadable, or any single line fails to parse,
the check reports what it can and flags the rest for manual review
rather than crashing startup.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# requirements.txt lives one level up from csf/ (the package root).
REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"

# Minimum nlm CLI version worth alerting on. Informational only — no hard
# block because the CLI is shelled out and yt-is's Python surface does not
# import it. Update this when a known-broken nlm version surfaces.
_NLM_KNOWN_GOOD = "0.8.9"


@dataclass
class DepStatus:
    """One requirement vs installed comparison."""

    name: str
    required: str  # raw specifier like ">=2.0.0", or "" if unconstrained
    installed: Optional[str]  # None when not installed
    ok: bool
    note: str = ""  # explanation when not ok or special-case


@dataclass
class DepCheckResult:
    """Aggregated result of a dependency check."""

    outdated: list[DepStatus] = field(default_factory=list)
    missing: list[DepStatus] = field(default_factory=list)
    ok: list[DepStatus] = field(default_factory=list)
    unparseable: list[tuple[str, str]] = field(default_factory=list)
    nlm_cli_version: Optional[str] = None
    nlm_cli_error: Optional[str] = None

    @property
    def has_problems(self) -> bool:
        return bool(self.outdated) or bool(self.missing)


def _parse_requirements(path: Path) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Parse requirements.txt into parsed entries plus unparseable lines.

    Returns ``(parsed, unparseable)`` where each parsed entry is
    ``(name, specifier_str, raw_line)`` and each unparseable entry is
    ``(raw_line, reason)``. Lines with environment markers are treated
    as unparseable because we do not evaluate marker context here.
    """
    try:
        from packaging.requirements import Requirement  # type: ignore
    except ImportError:
        return [], [("", "packaging library not installed; cannot parse requirements")]

    parsed: list[tuple[str, str, str]] = []
    unparseable: list[tuple[str, str]] = []
    if not path.exists():
        return [], [(str(path), "requirements.txt not found")]
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip inline comment after whitespace+# (PEP 508 separator).
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        try:
            req = Requirement(line)
        except Exception as e:  # packaging raises InvalidRequirement
            unparseable.append((raw_line, f"parse error: {e}"))
            continue
        if req.marker is not None:
            unparseable.append((raw_line, "environment marker present — not evaluated"))
            continue
        parsed.append((req.name, str(req.specifier), raw_line))
    return parsed, unparseable


def check_dependencies(requirements_path: Path | None = None) -> DepCheckResult:
    """Check all declared requirements against installed versions.

    Never raises — degrades gracefully on parse errors, missing
    ``packaging`` library, or unreadable requirements.txt.
    """
    path = requirements_path or REQUIREMENTS_PATH
    result = DepCheckResult()

    try:
        from packaging.version import Version, InvalidVersion  # type: ignore
        from packaging.specifiers import SpecifierSet  # type: ignore
    except ImportError:
        result.unparseable.append(("", "packaging library not installed; cannot check deps"))
        return result

    try:
        import importlib.metadata as importlib_metadata
    except ImportError:  # pragma: no cover — Python 3.8+ guarantee
        result.unparseable.append(("", "importlib.metadata not available; cannot check deps"))
        return result

    parsed, unparseable = _parse_requirements(path)
    result.unparseable.extend(unparseable)
    if not parsed:
        return result

    for name, specifier, raw_line in parsed:
        try:
            installed_str = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            result.missing.append(
                DepStatus(
                    name=name,
                    required=specifier,
                    installed=None,
                    ok=False,
                    note="not installed",
                )
            )
            continue
        except Exception as e:
            result.unparseable.append((raw_line, f"metadata lookup error: {e}"))
            continue

        if not specifier:
            result.ok.append(
                DepStatus(name=name, required="", installed=installed_str, ok=True)
            )
            continue

        try:
            spec = SpecifierSet(specifier)
            ok = Version(installed_str) in spec
        except InvalidVersion:
            # Installed version is not PEP 440 compliant (e.g. "0.0.0.0").
            # Cannot reliably evaluate — treat as OK with a note.
            result.ok.append(
                DepStatus(
                    name=name,
                    required=specifier,
                    installed=installed_str,
                    ok=True,
                    note="installed version not PEP 440 — cannot verify",
                )
            )
            continue
        except Exception as e:
            result.unparseable.append((raw_line, f"specifier error: {e}"))
            continue

        if ok:
            result.ok.append(
                DepStatus(name=name, required=specifier, installed=installed_str, ok=True)
            )
        else:
            result.outdated.append(
                DepStatus(
                    name=name,
                    required=specifier,
                    installed=installed_str,
                    ok=False,
                    note=f"need {specifier}, have {installed_str}",
                )
            )

    # Probe the nlm CLI that csf-source shells out to. Informational only.
    nlm_exe = shutil.which("nlm")
    if nlm_exe:
        try:
            proc = subprocess.run(
                [nlm_exe, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                m = re.search(r"(\d+\.\d+\.\d+)", proc.stdout)
                result.nlm_cli_version = m.group(1) if m else proc.stdout.strip()[:50]
            else:
                result.nlm_cli_error = f"nlm --version exited {proc.returncode}"
        except Exception as e:
            result.nlm_cli_error = f"nlm --version failed: {e}"
    else:
        result.nlm_cli_error = "nlm not on PATH"

    return result


def format_report(result: DepCheckResult) -> list[str]:
    """Format a DepCheckResult as human-readable stderr lines."""
    lines: list[str] = []
    nlm_hint = ""
    if result.nlm_cli_version:
        nlm_hint = f"nlm CLI version: {result.nlm_cli_version}"
        try:
            from packaging.version import Version  # type: ignore

            if Version(result.nlm_cli_version) < Version(_NLM_KNOWN_GOOD):
                nlm_hint += f" (older than known-good {_NLM_KNOWN_GOOD} — consider `pip install -U notebooklm-mcp-cli`)"
        except Exception:
            pass
    elif result.nlm_cli_error:
        nlm_hint = f"nlm CLI: {result.nlm_cli_error}"

    if not result.has_problems and not result.unparseable:
        lines.append(
            f"[deps] OK - {len(result.ok)} packages meet requirements.txt minimums."
        )
        if nlm_hint:
            lines.append(f"[deps] {nlm_hint}")
        return lines

    if result.outdated:
        lines.append(f"[deps] OUTDATED ({len(result.outdated)}):")
        for d in result.outdated:
            lines.append(
                f"  - {d.name}: installed {d.installed}, requirements.txt requires {d.required}"
            )
    if result.missing:
        lines.append(f"[deps] MISSING ({len(result.missing)}):")
        for d in result.missing:
            lines.append(
                f"  - {d.name}: not installed (requires {d.required})"
            )
    if result.unparseable:
        lines.append(
            f"[deps] UNPARSEABLE ({len(result.unparseable)}) - manual review needed:"
        )
        for raw, reason in result.unparseable:
            suffix = f" - {reason}" if reason else ""
            lines.append(f"  - {raw}{suffix}")
    lines.append(f"[deps] {len(result.ok)} packages OK.")
    if nlm_hint:
        lines.append(f"[deps] {nlm_hint}")
    lines.append("[deps] Set YTIS_STRICT_DEPS=1 to fail-fast on outdated packages.")
    lines.append("[deps] Set YTIS_SKIP_DEPS_CHECK=1 to silence this check.")
    return lines


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def run_check(
    *,
    strict: bool | None = None,
    skip: bool | None = None,
    stream=None,
) -> int:
    """Run the dependency check and emit a report.

    Args:
        strict: If True, return 1 on outdated/missing. None reads YTIS_STRICT_DEPS.
        skip: If True, do nothing and return 2. None reads YTIS_SKIP_DEPS_CHECK.
        stream: Output stream (default sys.stderr).

    Returns:
        0 if OK or warn-mode, 1 if strict-mode and problems found, 2 if skipped.
    """
    if skip is None:
        skip = _env_truthy("YTIS_SKIP_DEPS_CHECK")
    if skip:
        return 2

    if strict is None:
        strict = _env_truthy("YTIS_STRICT_DEPS")

    out = stream or sys.stderr
    result = check_dependencies()
    for line in format_report(result):
        print(line, file=out)

    if strict and result.has_problems:
        return 1
    return 0
