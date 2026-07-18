#!/usr/bin/env python3
"""PreToolUse guard: block direct `git worktree` Bash invocations.

yt-is has a managed worktree lifecycle (see P:/docs/worktree-lifecycle-design.md
and P:/packages/yt-is/HANDOFF.md). Raw `git worktree` invocations bypass:

  - naming policy (`worktree-policy.toml`)
  - tracked-parent guards
  - HANDOFF.md sentinel sync
  - backup-tag preservation for unreachable commits
  - the per-terminal registry in `_resolve_state_dir()`

This hook fires on `Bash` tool calls. If the command contains a `git worktree`
subcommand (case-insensitive), it blocks the call by default.

Escape hatch: set `GO_WORKTREE_SAFETY_BYPASS=1` in the environment for the
duration of any command you legitimately need to bypass. The hook will then
emit an advisory warning to stderr and allow the call. Or remove the hook
entry from `P:/packages/yt-is/.claude/settings.json`.

Fail-safe: malformed payload or unparseable input => allow (return 0). We
never want a hook parse error to silently block legitimate work.

Registration: P:/packages/yt-is/.claude/settings.json, PreToolUse matcher
for `Bash`, command points at this file.
"""
from __future__ import annotations

import json
import os
import re
import sys


_GIT_WT_PATTERN = re.compile(r"\bgit\s+worktree\b", re.IGNORECASE)


def main() -> int:
    # Read payload from stdin (Claude Code / Grok Build convention)
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        # Fail-silent: malformed payload => allow
        return 0

    # Only intercept Bash tool calls
    tool_name = payload.get("tool_name", "")
    if tool_name != "Bash":
        return 0

    tool_input = payload.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        return 0

    # Allow non-worktree commands unconditionally
    if not _GIT_WT_PATTERN.search(command):
        return 0

    # Escape hatch: explicit bypass env var
    if os.environ.get("GO_WORKTREE_SAFETY_BYPASS", "").strip() == "1":
        print(
            "WORKTREE_POLICY: bypassing git worktree guard "
            "(GO_WORKTREE_SAFETY_BYPASS=1). Use the managed CLI for the "
            "policy-validated path.",
            file=sys.stderr,
        )
        return 0

    # Block: deny with actionable reason
    deny = {
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "WORKTREE_POLICY: direct `git worktree` invocations are blocked to enforce "
            "the managed worktree lifecycle (see P:/docs/worktree-lifecycle-design.md "
            "and P:/packages/yt-is/HANDOFF.md). Use the managed path instead. "
            "To acknowledge a one-off bypass, set GO_WORKTREE_SAFETY_BYPASS=1 "
            "for the duration of this command."
        ),
    }
    print(json.dumps(deny))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())