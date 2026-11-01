"""Invoker step 1a against the real MCP Apps fixture Server (F7-08; API §4.8, delta D1).

Two Servers run ``apps_server.py`` over modern stdio (``apps`` and ``other``). An App of
``apps`` may call only ``apps``' App-visible tools; every other attempt ends ``denied`` /
``app_scope`` without a consent prompt, is audited once and never reaches a Server (the
fixture's ``call_counts``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.audit import AuditQuery
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome
from reflex_mcphost.core.types import Consumer
from tests.integration.test_host import TIMEOUT, Setup, _toml_server, setup
from tests.integration.test_invoker import FakePrompter, wait_tools, write_config

pytestmark = pytest.mark.anyio
__all__ = ["setup"]
APP = Consumer.app("apps")
Out = InvocationOutcome


@pytest.fixture
async def host(setup: Setup, apps_modern_stdio: StdioServerParameters) -> AsyncIterator[Host]:
    table = _toml_server([apps_modern_stdio.command, *apps_modern_stdio.args])
    prompter = FakePrompter()
    host = setup.host(write_config(setup, {"apps": table, "other": table}), prompter=prompter)
    with anyio.fail_after(TIMEOUT * 3):
        async with host:
            await wait_tools(host, "apps")
            await wait_tools(host, "other")
            yield host


async def _counts(host: Host, server_id: str) -> dict[str, int]:
    result = await host.invoker.call(f"{server_id}::call_counts", {}, consumer=Consumer.human())
    assert result.ok, result.error
    counts: dict[str, int] = json.loads(result.text)
    counts.pop("call_counts", None)
    return counts


@pytest.mark.parametrize(
    ("fqn", "reason"),
    [("apps::model_only_echo", "not_app_visible"), ("other::app_only_echo", "foreign_server")],
)
async def test_out_of_scope_calls_never_reach_a_server(host: Host, fqn: str, reason: str) -> None:
    """REQ-APP-004, REQ-APP-008 (Art. 3): ``denied`` / ``app_scope`` with the reason, no
    consent prompt, one audit record (``not_evaluated``), and the fixture Server never saw
    the call."""
    prompter = host.prompter
    assert isinstance(prompter, FakePrompter)
    result = await host.invoker.call(fqn, {"text": "x"}, consumer=APP)
    assert result.outcome is Out.denied
    assert result.error is not None
    assert (result.error.code, result.error.details) == ("app_scope", {"reason": reason})
    assert prompter.asked == []
    records = (await host.audit.query(AuditQuery(consumer=APP))).records
    assert [(r.invocation_id, r.decision_source, r.error_code) for r in records] == [
        (result.id, "not_evaluated", "app_scope")
    ]
    assert await _counts(host, "apps") == {}
    assert await _counts(host, "other") == {}


@pytest.mark.parametrize("tool", ["app_only_echo", "probe"])
async def test_own_app_visible_tool_is_consented_and_called(host: Host, tool: str) -> None:
    """REQ-APP-004 (API §4.8): an App's call of its own App-visible tool asks consent for
    ``app:apps``, reaches the Server once and is audited once with that Consumer."""
    prompter = host.prompter
    assert isinstance(prompter, FakePrompter)
    result = await host.invoker.call(f"apps::{tool}", {"text": "hello"}, consumer=APP)
    assert result.outcome is Out.success, result.error
    assert [str(r.consumer) for r in prompter.asked] == ["app:apps"]
    (record,) = (await host.audit.query(AuditQuery(consumer=APP))).records
    assert (record.invocation_id, record.outcome) == (result.id, Out.success)
    assert await _counts(host, "apps") == {tool: 1}


async def test_human_and_agent_calls_unaffected(host: Host) -> None:
    """REQ-APP-008: the App scope does not apply to ``human:ui`` or ``agent:x`` — both may
    call the model-only tool (consent asked for each)."""
    for consumer in (Consumer.human(), Consumer.agent("x")):
        result = await host.invoker.call("apps::model_only_echo", {"text": "m"}, consumer=consumer)
        assert result.outcome is Out.success, result.error
    assert await _counts(host, "apps") == {"model_only_echo": 2}
