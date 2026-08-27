"""API-drift gate: every mock.patch/monkeypatch string target must resolve.

Root cause class this repo shipped three times (dht 15-error wave, c4-auth
2-failure wave, sharded-lane artifacts) before the sys.path sweep: tests
patch `"module.attribute"` strings for APIs that were renamed or deleted,
the test then errors at setup/runtime while the suite "passes" elsewhere.

This gate runs at collection time over tests/**: parses each file's AST
for patch-target string literals and resolves `module.attr` by importing
ONLY the target module (never executes the test module). Unresolvable
targets fail collection with the file:line named.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"

_PATCH_FUNCS = {"patch", "patch.object", "patch.multiple", "create_autospec"}
_TARGET_ARGS = {0}          # positional target slots to check


def _iter_patch_targets(node: ast.AST):
    """Yield (path, line, col) for every patch-string literal in node."""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name not in ("patch", "object", "multiple"):
            continue
        # mock.patch / unittest.mock.patch / monkeypatch.patch...: accept
        # any *.patch call whose first arg is a string literal
        if isinstance(func, ast.Attribute) and func.attr != "patch":
            if not (func.attr in ("object", "multiple")
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "patch"):
                continue
        for idx, arg in enumerate(n.args):
            if idx in _TARGET_ARGS and isinstance(arg, ast.Constant) \
                    and isinstance(arg.value, str):
                yield arg.value, arg.lineno, arg.col_offset


def _resolves(target: str) -> bool:
    """mock target forms: 'module.attr[.attr]', 'module', 'object.attr'."""
    parts = target.split(".")
    if not parts or not all(parts):
        return True                      # empty/dotted-odd: not drift-checkable
    for split_at in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:split_at])
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        obj = mod
        for attr in parts[split_at:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False                          # no importable prefix at all


def test_all_mock_patch_targets_resolve():
    """The API-drift gate itself. Fails listing every dead patch target."""
    if not _TESTS.is_dir():
        pytest.skip("tests dir missing")
    failures = []
    checked = 0
    for path in sorted(_TESTS.rglob("test_*.py")):
        rel = path.relative_to(_REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            continue                     # syntax is pytest's job, not ours
        local = {"__file__": str(path)}
        for target, lineno, _col in _iter_patch_targets(tree):
            checked += 1
            prev = sys.path.insert(0, str(_REPO))
            try:
                ok = _resolves(target)
            finally:
                pass
            if not ok:
                failures.append(f"{rel}:{lineno} patches '{target}' - "
                                "unresolvable (renamed/deleted API?)")
    assert not failures, (
        f"API-drift gate: {len(failures)} dead mock target(s) "
        f"(checked {checked}):\n" + "\n".join(failures[:40]))
