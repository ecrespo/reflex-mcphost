"""The Catalog invoke flow against a real Host, modern and legacy stdio (F6-05; REQ-UI-004).

The Host is served through ``HostRuntime``; the ``HostState`` background handlers
(``invoke_tool``, ``cancel_invocation``, ``read_selected``) run on a ``Tab``; a
programmatic allow rule for ``human:ui`` decides consent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import pytest

from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.ui import models
from reflex_mcphost.ui.state import HostState
from tests.integration.toolset.rig import Setup, era_server, host_for, records, setup
from tests.integration.ui.rig import Tab
from tests.unit.ui.rig import published

pytestmark = pytest.mark.anyio
__all__ = ["era_server", "setup"]  # re-exported fixtures
HUMAN = Consumer.human()


@asynccontextmanager
async def ui_host(
    setup: Setup, era_server: tuple[str, str]
) -> AsyncIterator[tuple[Host, str, Tab]]:
    async with host_for(setup, era_server) as (host, server_id):
        await host.consent.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.server(server_id),
            consumer=HUMAN,
        )
        tab = Tab()
        tab.state.tools = models.catalog_rows("tools", host.catalog.tools(server_id))
        tab.state.resource_templates = models.catalog_rows(
            "resource_templates", host.catalog.resource_templates(server_id)
        )
        tab.state.catalog_prompts = models.catalog_rows("prompts", host.catalog.prompts(server_id))
        with published(host):
            yield host, server_id, tab


async def _invoke(tab: Tab, fqn: str, form: dict[str, object]) -> dict[str, object]:
    tab.sync(HostState.select_entry, fqn.split("::")[0], fqn)
    assert tab.state.catalog_selected["fqn"] == fqn
    await tab.run(HostState.invoke_tool, form)
    result: dict[str, object] = tab.state.invocation["result"]
    return result


async def test_results_errors_and_audit(setup: Setup, era_server: tuple[str, str]) -> None:
    """REQ-UI-004, REQ-INV-006 (both eras): ``echo`` shows the echoed text; ``structured``
    the JSON panel; ``tool_error`` an error badge with the Server's text; invalid
    arguments (JSON mode) ``invalid_arguments`` with the issue mapped to its field; the
    audit records name ``human:ui``."""
    async with ui_host(setup, era_server) as (host, server_id, tab):
        echo = await _invoke(tab, f"{server_id}::echo", {"text": "hi"})
        assert (echo["outcome"], echo["blocks"][0]["text"]) == ("success", "hi")  # type: ignore[index]
        structured = await _invoke(tab, f"{server_id}::structured", {})
        assert json.loads(structured["structured"]) == {"value": 42}  # type: ignore[arg-type]
        failed = await _invoke(tab, f"{server_id}::tool_error", {})
        assert (failed["outcome"], failed["ok"]) == ("tool_error", False)
        assert "tool failed" in json.dumps(failed["blocks"])
        invalid = await _invoke(tab, f"{server_id}::echo", {models.JSON_FIELD: '{"text": 5}'})
        assert invalid["outcome"] == "invalid_arguments"
        assert set(tab.state.form_errors) == {"text"}
        tab.sync(HostState.select_entry, server_id, f"{server_id}::echo")
        await tab.run(HostState.invoke_tool, {})  # stopped by the form: nothing is sent
        assert tab.state.form_errors == {"text": "this field is required"}
        assert tab.state.invocation == {}
        rows = await records(host, fqn=Fqn.parse(f"{server_id}::echo"))
        assert {r.consumer for r in rows} == {HUMAN}
        assert len(rows) == 2


async def test_progress_and_cancel(setup: Setup, era_server: tuple[str, str]) -> None:
    """REQ-UI-004, REQ-INV-004, REQ-INV-005 (both eras): ``progress`` shows every progress
    value; ``slow`` + Cancel ends ``cancelled``."""
    async with ui_host(setup, era_server) as (_, server_id, tab):
        done = await _invoke(tab, f"{server_id}::progress", {"steps": "3"})
        assert done["outcome"] == "success"
        assert [p["text"] for p in tab.state.invocation["progress"]] == [
            "1/3 step 1",
            "2/3 step 2",
            "3/3 step 3",
        ]
        tab.sync(HostState.select_entry, server_id, f"{server_id}::slow")
        async with anyio.create_task_group() as tg:
            tg.start_soon(tab.run, HostState.invoke_tool, {"seconds": "30"})
            await tab.until(lambda s: s.invocation.get("running") is True)
            await tab.run(HostState.cancel_invocation)
            await tab.until(lambda s: s.invocation.get("running") is False)
        assert tab.state.invocation["result"]["outcome"] == "cancelled"


async def test_resource_template_and_prompt_reads(
    setup: Setup, era_server: tuple[str, str]
) -> None:
    """REQ-CAT-005, REQ-CAT-006, REQ-CAT-007 (UI side, both eras): a resource read, a
    template read from its variables form and a prompt get show their content."""
    async with ui_host(setup, era_server) as (host, server_id, tab):
        tab.state.resources = models.catalog_rows("resources", host.catalog.resources(server_id))
        tab.sync(HostState.set_catalog_tab, "resources")
        tab.sync(HostState.select_entry, server_id, "fixture://hello")
        await tab.run(HostState.read_selected, {})
        assert tab.state.read_result["blocks"][0]["text"] == "hello world"
        tab.sync(HostState.set_catalog_tab, "templates")
        [template] = tab.state.catalog_shown
        tab.sync(HostState.select_entry, server_id, template["id"])
        assert tab.state.catalog_selected["variables"] == ["item"]
        await tab.run(HostState.read_selected, {"item": "7"})
        assert tab.state.read_result["blocks"][0]["text"] == "item 7"
        tab.sync(HostState.set_catalog_tab, "prompts")
        tab.sync(HostState.select_entry, server_id, f"{server_id}::greet")
        await tab.run(HostState.read_selected, {"name": "Ada"})
        [message] = tab.state.read_result["blocks"]
        assert (message["role"], message["text"]) == ("user", "Hi Ada")
