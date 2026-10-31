"""Fixture Servers answer a raw mcp.Client in both eras (Tech Design §8, F1-10)."""

from __future__ import annotations

import pytest
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.anyio

MODERN_MIN = "2026-07-28"
LEGACY_VERSION = "2025-11-25"
EXPECTED_TOOLS = {"echo", "structured", "slow", "progress", "ask_name", "stderr", "crash"}


async def _check(server: StdioServerParameters | str, *, modern: bool) -> None:
    async with Client(server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools >= EXPECTED_TOOLS
        if modern:
            assert client.protocol_version >= MODERN_MIN
        else:
            assert client.protocol_version == LEGACY_VERSION
        result = await client.call_tool("echo", {"text": "ping"})
        assert result.content[0].text == "ping"  # type: ignore[union-attr]


async def test_modern_stdio(modern_stdio: StdioServerParameters) -> None:
    """Tech Design §8: modern stdio fixture lists tools at protocol >= 2026-07-28."""
    await _check(modern_stdio, modern=True)


async def test_modern_http(modern_http: str) -> None:
    """Tech Design §8: modern HTTP fixture lists tools at protocol >= 2026-07-28."""
    await _check(modern_http, modern=True)


@pytest.mark.interop
async def test_legacy_stdio(legacy_stdio: StdioServerParameters) -> None:
    """Tech Design §8: legacy stdio fixture negotiates 2025-11-25."""
    await _check(legacy_stdio, modern=False)


@pytest.mark.interop
async def test_legacy_http(legacy_http: str) -> None:
    """Tech Design §8: legacy HTTP fixture negotiates 2025-11-25 with Mcp-Session-Id."""
    await _check(legacy_http, modern=False)


async def test_modern_surface(modern_stdio: StdioServerParameters) -> None:
    """Tech Design §8: resources, the template and the prompt are exposed."""
    async with Client(modern_stdio) as client:
        assert [r.uri for r in (await client.list_resources()).resources] == ["fixture://hello"]
        templates = (await client.list_resource_templates()).resource_templates
        assert [t.uri_template for t in templates] == ["fixture://items/{item}"]
        assert [p.name for p in (await client.list_prompts()).prompts] == ["greet"]
