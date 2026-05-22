"""NotebookLM CLI bootstrap helpers.

This module refreshes yt-is toward the latest NotebookLM CLI by running a
single `uv tool install --upgrade notebooklm-mcp-cli` probe per process when
NLM workflows first need the CLI, then falls back to the known-good pinned
spec if the latest build breaks `nlm login --check` on this machine.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

_AUTO_UPDATE_PACKAGE = "notebooklm-mcp-cli"
_AUTO_UPDATE_SPEC = _AUTO_UPDATE_PACKAGE
_AUTO_UPDATE_ENV = "YTIS_NLM_AUTO_UPDATE"
_AUTO_UPDATE_TIMEOUT_ENV = "YTIS_NLM_AUTO_UPDATE_TIMEOUT_S"
_EXPLICIT_CLI_ENV = "YTIS_NLM_CLI"
_FALLBACK_SPEC_ENV = "YTIS_NLM_FALLBACK_SPEC"
_DEFAULT_FALLBACK_SPEC = (
    "notebooklm-mcp-cli @ git+https://github.com/jacob-bd/notebooklm-mcp-cli.git@"
    "3711e782cfa63db948bd34f9ae6e97210821223c"
)
_CLI_PROBE_ERROR_MARKERS = (
    "Failed to canonicalize script path",
)

_bootstrap_lock = threading.Lock()
_bootstrap_attempted = False


def _auto_update_enabled() -> bool:
    value = os.getenv(_AUTO_UPDATE_ENV, "1").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _auto_update_timeout_s(default: float = 900.0) -> float:
    raw = os.getenv(_AUTO_UPDATE_TIMEOUT_ENV, "").strip()
    if not raw:
        return default
    try:
        timeout_s = float(raw)
    except ValueError:
        return default
    return max(1.0, timeout_s)


def _summarize_output(stdout: str, stderr: str) -> str:
    for text in (stderr, stdout):
        stripped = text.strip()
        if stripped:
            return stripped.splitlines()[0][:500]
    return ""


def _fallback_spec() -> str:
    return os.getenv(_FALLBACK_SPEC_ENV, "").strip() or _DEFAULT_FALLBACK_SPEC


def _repo_wrapper_path() -> Path:
    return Path(__file__).resolve().parents[1] / "bin" / "csf-nlm-wrapper.cmd"


def get_nlm_executable() -> str:
    override = os.getenv(_EXPLICIT_CLI_ENV, "").strip()
    if override:
        return override
    wrapper = _repo_wrapper_path()
    if wrapper.exists():
        return str(wrapper)
    return "nlm"


def _run_install(spec: str, *, upgrade: bool) -> subprocess.CompletedProcess:
    uv_executable = shutil.which("uv")
    if not uv_executable:
        raise RuntimeError("uv is required to install notebooklm-mcp-cli")
    cmd = [uv_executable, "tool", "install"]
    if upgrade:
        cmd.append("--upgrade")
    cmd.append(spec)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_auto_update_timeout_s(),
        check=False,
    )


def _login_probe_result() -> subprocess.CompletedProcess:
    probe_cmd = [get_nlm_executable(), "login", "--check"]
    profile = os.getenv("NOTEBOOKLM_PROFILE", "").strip()
    if profile:
        probe_cmd.extend(["--profile", profile])
    return subprocess.run(
        probe_cmd,
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )


def _probe_signals_cli_breakage(result: subprocess.CompletedProcess) -> bool:
    combined = f"{result.stdout or ''}\n{result.stderr or ''}"
    return any(marker in combined for marker in _CLI_PROBE_ERROR_MARKERS)


def reset_nlm_bootstrap_state() -> None:
    """Reset the per-process bootstrap sentinel.

    Tests use this to exercise the install path repeatedly without reloading
    the module.
    """
    global _bootstrap_attempted
    with _bootstrap_lock:
        _bootstrap_attempted = False


def ensure_latest_nlm_cli() -> None:
    """Install or refresh the NotebookLM CLI once per process.

    The bootstrap is skipped when `YTIS_NLM_AUTO_UPDATE=0` or when an explicit
    `YTIS_NLM_CLI` override is set. If the refresh attempt fails but a usable
    `nlm` executable is already present, yt-is keeps going and logs a warning.
    """
    if not _auto_update_enabled():
        return
    if os.getenv(_EXPLICIT_CLI_ENV, "").strip():
        return

    global _bootstrap_attempted
    with _bootstrap_lock:
        if _bootstrap_attempted:
            return
        _bootstrap_attempted = True

    if not shutil.which("uv"):
        if shutil.which("nlm"):
            logging.warning(
                "[nlm-bootstrap] uv was not found, so yt-is kept the existing NotebookLM CLI."
            )
            return
        raise RuntimeError(
            "uv is required to install notebooklm-mcp-cli, but neither uv nor nlm was found on PATH"
        )

    try:
        result = _run_install(_AUTO_UPDATE_SPEC, upgrade=True)
    except subprocess.TimeoutExpired as exc:
        if shutil.which("nlm"):
            logging.warning(
                "[nlm-bootstrap] NotebookLM CLI refresh timed out; continuing with existing nlm"
            )
            return
        raise RuntimeError("NotebookLM CLI refresh timed out and no nlm executable is available") from exc
    except OSError as exc:
        if shutil.which("nlm"):
            logging.warning(
                "[nlm-bootstrap] NotebookLM CLI refresh could not start; continuing with existing nlm"
            )
            return
        raise RuntimeError("NotebookLM CLI refresh could not start and no nlm executable is available") from exc

    if result.returncode != 0:
        summary = _summarize_output(result.stdout or "", result.stderr or "")
        if shutil.which("nlm"):
            message = "[nlm-bootstrap] NotebookLM CLI refresh failed; continuing with existing nlm"
            if summary:
                message = f"{message}: {summary}"
            logging.warning(message)
            return
        raise RuntimeError(
            "NotebookLM CLI refresh failed and no nlm executable is available"
            + (f": {summary}" if summary else "")
        )

    probe = _login_probe_result()
    if not _probe_signals_cli_breakage(probe):
        logging.info("[nlm-bootstrap] refreshed NotebookLM CLI with uv tool install --upgrade notebooklm-mcp-cli")
        return

    fallback_spec = _fallback_spec()
    logging.warning(
        "[nlm-bootstrap] latest NotebookLM CLI probe failed, falling back to %s",
        fallback_spec,
    )
    fallback_result = _run_install(fallback_spec, upgrade=False)
    if fallback_result.returncode != 0:
        summary = _summarize_output(fallback_result.stdout or "", fallback_result.stderr or "")
        if shutil.which("nlm"):
            message = "[nlm-bootstrap] NotebookLM CLI fallback refresh failed; continuing with existing nlm"
            if summary:
                message = f"{message}: {summary}"
            logging.warning(message)
            return
        raise RuntimeError(
            "NotebookLM CLI fallback refresh failed and no nlm executable is available"
            + (f": {summary}" if summary else "")
        )
    logging.info("[nlm-bootstrap] restored NotebookLM CLI using fallback %s", fallback_spec)
