from __future__ import annotations

from typing import Literal

import pytest

from ef.mcp_server import select_http_transport


class _Modern:
    def run(self, transport: Literal["stdio", "sse", "streamable-http"] = "stdio"):
        pass


class _Legacy:
    def run(self, transport: Literal["stdio", "sse"] = "stdio"):
        pass


class _StdioOnly:
    def run(self, transport: Literal["stdio"] = "stdio"):
        pass


def test_selects_streamable_http_when_runtime_exposes_it():
    assert select_http_transport(_Modern()) == "streamable-http"


def test_selects_sse_for_legacy_fastmcp_runtime():
    assert select_http_transport(_Legacy()) == "sse"


def test_fails_closed_when_runtime_has_no_http_transport():
    with pytest.raises(RuntimeError, match="no supported HTTP transport"):
        select_http_transport(_StdioOnly())
