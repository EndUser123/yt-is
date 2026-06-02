"""Helpers for blocking accidental live NotebookLM processes in pytest."""

from __future__ import annotations

from collections.abc import Iterable


_LIVE_NOTEBOOKLM_EXECUTABLES = {
    "csf-nlm-wrapper.cmd",
    "nlm",
    "nlm.exe",
}


def _normalize_command_text(cmd: object) -> str:
    if cmd is None:
        return ""
    if isinstance(cmd, bytes):
        text = cmd.decode("utf-8", errors="ignore")
    elif isinstance(cmd, str):
        text = cmd
    elif isinstance(cmd, Iterable):
        text = " ".join(str(part) for part in cmd)
    else:
        text = str(cmd)
    return text.replace("\\", "/").lower()


def _command_executable_name(cmd: object) -> str:
    if isinstance(cmd, Iterable) and not isinstance(cmd, (bytes, str)):
        parts = [str(part).replace("\\", "/").lower() for part in cmd if str(part)]
        if not parts:
            return ""
        executable = parts[0]
    else:
        text = _normalize_command_text(cmd)
        if not text:
            return ""
        executable = text.split(maxsplit=1)[0]
    return executable.rsplit("/", 1)[-1]


def is_live_notebooklm_command(cmd: object) -> bool:
    """Return True when a command would launch live NotebookLM auth or workers."""
    text = _normalize_command_text(cmd)
    if not text:
        return False
    if "dev.worker_pool.worker_main" in text:
        return True
    if "login --force" not in text:
        return False
    executable_name = _command_executable_name(cmd)
    return executable_name in _LIVE_NOTEBOOKLM_EXECUTABLES


def describe_live_notebooklm_command(cmd: object) -> str:
    text = _normalize_command_text(cmd)
    return text if len(text) <= 240 else f"{text[:237]}..."
