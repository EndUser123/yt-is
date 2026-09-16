#!/usr/bin/env python3
"""Read-only preflight for the EF warm-query WinSW runtime.

The warm service has two useful Python views: a machine-only diagnostic view
and the exact environment declared by its WinSW XML.  The XML environment is
the runtime authority: it explicitly composes the machine site-packages with
the service's user overlay.  The machine-only view is retained as a warning
surface so an operator can see what the base interpreter provides without
mistaking it for the service's import path.  This command never installs
packages, edits XML, or starts a service.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SERVICE_XML = REPO / "deploy" / "winsw" / "ef_warm_query.xml"
VERSION_CONTRACT = REPO / "config" / "version-contract.json"
PACKAGE_IMPORTS = {
    "numpy": "numpy",
    "qdrant-client": "qdrant_client",
    "psutil": "psutil",
    "fasteners": "fasteners",
    "notebooklm-py": "notebooklm",
    "typing_extensions": "typing_extensions",
    "torch": "torch",
    "sentence-transformers": "sentence_transformers",
    "transformers": "transformers",
    "tokenizers": "tokenizers",
    "FlagEmbedding": "FlagEmbedding",
    "mcp": "mcp",
}


def _service_config(path: Path) -> dict:
    root = ET.parse(path).getroot()
    env = {
        node.attrib["name"]: node.attrib.get("value", "")
        for node in root.findall("env") if "name" in node.attrib
    }
    return {
        "executable": root.findtext("executable") or "",
        "workingdirectory": root.findtext("workingdirectory") or "",
        "environment": env,
    }


def _probe(executable: str, *, environment: dict[str, str] | None = None,
           disable_user_site: bool = True) -> dict:
    code = """
import importlib
import importlib.metadata
import json
import os
packages = json.loads(os.environ['EF_DEP_PROBE_PACKAGES'])
out = {}
for dist, module in packages.items():
    row = {}
    try:
        row['version'] = importlib.metadata.version(dist)
    except Exception as exc:
        row['version_error'] = type(exc).__name__
    try:
        mod = importlib.import_module(module)
        row['import'] = True
        row['path'] = getattr(mod, '__file__', '')
    except Exception as exc:
        row['import'] = False
        row['import_error'] = f'{type(exc).__name__}: {exc}'
    out[dist] = row
print(json.dumps(out, sort_keys=True))
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if environment is not None:
        env.update(environment)
    env["EF_DEP_PROBE_PACKAGES"] = json.dumps(PACKAGE_IMPORTS)
    command = [executable]
    if disable_user_site:
        command.append("-s")
    command.extend(["-c", code])
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, env=env, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"probe_error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {"probe_error": result.stderr.strip() or result.stdout.strip()}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"probe_error": "probe emitted non-JSON output",
                "stdout": result.stdout[-1000:]}


def _expected(contract: dict) -> dict[str, str]:
    encoder = contract["components"]["encoder-stack"]["version"]
    return {
        **{k: str(v) for k, v in encoder.items()},
        "mcp": str(contract["components"]["mcp"]["version"]),
    }


def _evaluate(machine: dict, service: dict, expected: dict[str, str]) -> list[str]:
    """Return only blocking failures from the exact service environment.

    ``machine`` is intentionally not evaluated here.  With ``-s`` it omits
    the explicit user overlay that WinSW places on ``PYTHONPATH``; requiring
    that diagnostic view to contain the model stack falsely blocks a service
    whose declared runtime is healthy.
    """
    reasons: list[str] = []
    if "probe_error" in service:
        return [f"service probe failed: {service['probe_error']}"]
    for package, version in expected.items():
        row = service.get(package, {})
        if not row.get("import"):
            detail = row.get("import_error") or row.get("version_error") or "missing"
            reasons.append(f"service {package} import failed: {detail}")
        elif row.get("version") != version:
            reasons.append(
                f"service {package} version {row.get('version')!r} "
                f"does not match {version!r}"
            )
    return reasons


def _machine_warnings(machine: dict, expected: dict[str, str]) -> list[str]:
    """Explain base-interpreter drift without turning it into a false block."""
    if "probe_error" in machine:
        return [f"machine-only probe failed: {machine['probe_error']}"]
    warnings: list[str] = []
    for package, version in expected.items():
        row = machine.get(package, {})
        if not row.get("import"):
            detail = row.get("import_error") or row.get("version_error") or "missing"
            warnings.append(f"machine-only {package} unavailable: {detail}")
        elif row.get("version") != version:
            warnings.append(
                f"machine-only {package} version {row.get('version')!r} "
                f"differs from {version!r}"
            )
    return warnings


def run(*, service_xml: Path = SERVICE_XML,
        contract_path: Path = VERSION_CONTRACT) -> dict:
    config = _service_config(service_xml)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    executable = config["executable"]
    machine = _probe(executable, disable_user_site=True)
    service = _probe(executable, environment=config["environment"],
                     disable_user_site=True)
    expected = _expected(contract)
    reasons = _evaluate(machine, service, expected)
    return {
        "status": "READY" if not reasons else "BLOCKED",
        "read_only": True,
        "service_xml": str(service_xml),
        "executable": executable,
        "machine": machine,
        "service_environment": service,
        "machine_warnings": _machine_warnings(machine, expected),
        "reasons": reasons,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-xml", type=Path, default=SERVICE_XML)
    parser.add_argument("--contract", type=Path, default=VERSION_CONTRACT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = run(service_xml=args.service_xml, contract_path=args.contract)
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
