"""``Toolset`` over a fake catalog, statuses and Invoker (F5-01; API §4.1, §4.12)."""

from __future__ import annotations

import pytest

from reflex_mcphost.core.connection.state import ServerState
from reflex_mcphost.core.errors import HostClosedError
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.toolset import ToolCallOutcome, Toolset, ToolSpec
from tests.unit.toolset.fakes import HELPER, Rig, tool

pytestmark = pytest.mark.anyio


def test_list_returns_specs_of_ready_fresh_tools() -> None:
    """REQ-TLS-001, REQ-CAT-004: list() exposes name, description and JSON input schema of
    tools of ready Servers; non-ready Servers' and stale tools are excluded."""
    rig = Rig()
    rig.catalog.entries = [
        tool("srv::echo"),
        tool("srv::old", stale=True),
        tool("down::echo"),
        tool("auth::echo"),
    ]
    rig.statuses.states = {"down": ServerState.RECONNECTING, "auth": ServerState.AUTH_REQUIRED}
    specs = rig.toolset().list()
    assert [s.name for s in specs] == ["srv__echo"]
    (spec,) = specs
    assert isinstance(spec, ToolSpec)
    assert spec.fqn == Fqn.parse("srv::echo")
    assert spec.input_schema == tool("srv::echo").input_schema
    assert spec.output_schema is None


def test_unknown_server_status_counts_as_not_ready() -> None:
    """REQ-TLS-001: a catalog entry whose Server just left the registry is not listed."""
    from reflex_mcphost.core.errors import UnknownServerError

    rig = Rig()
    rig.catalog.entries = [tool("gone::echo"), tool("srv::echo")]

    def status_of(server_id: str) -> object:
        if server_id == "gone":
            raise UnknownServerError(server_id)
        return rig.statuses(server_id)

    toolset = Toolset(
        catalog=rig.catalog,
        status_of=status_of,  # type: ignore[arg-type]
        invoker_of=lambda: rig.invoker,
        consumer=HELPER,
    )
    assert [s.name for s in toolset.list()] == ["srv__echo"]


@pytest.mark.parametrize(
    ("description", "title", "expected"),
    [
        ("Echo the text.", "Echo", "[srv] Echo the text."),
        (None, "Echo", "[srv] Echo"),
        (None, None, "[srv] echo"),
        ("", "Echo", "[srv] Echo"),
    ],
)
def test_description_format(description: str | None, title: str | None, expected: str) -> None:
    """REQ-TLS-001: description = "[server_id] " + (description or title or name)."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo", description=description, title=title)]
    assert rig.toolset().list()[0].description == expected


def test_catalog_change_is_visible_on_next_list() -> None:
    """REQ-TLS-004: the listing reads the catalog on every call (no caching)."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    toolset = rig.toolset()
    assert [s.name for s in toolset.list()] == ["srv__echo"]
    rig.catalog.entries.append(tool("srv::added"))
    assert [s.name for s in toolset.list()] == ["srv__echo", "srv__added"]
    rig.statuses.states["srv"] = ServerState.FAILED
    assert toolset.list() == []


def test_returned_schema_is_a_copy() -> None:
    """REQ-TLS-001, API §3.2: mutating a returned schema leaves the catalog untouched."""
    rig = Rig()
    output = {"type": "object", "properties": {"v": {"type": "integer"}}}
    entry = tool("srv::echo").model_copy(update={"output_schema": output})
    rig.catalog.entries = [entry]
    spec = rig.toolset().list()[0]
    spec.input_schema["properties"]["text"]["type"] = "integer"
    spec.input_schema["injected"] = True
    assert spec.output_schema is not None
    spec.output_schema["properties"].clear()
    assert entry.input_schema == tool("srv::echo").input_schema
    assert entry.output_schema == {"type": "object", "properties": {"v": {"type": "integer"}}}
    assert rig.toolset().list()[0].input_schema == tool("srv::echo").input_schema


def test_resolve_maps_alias_back() -> None:
    """REQ-TLS-002: resolve(alias) == fqn; an unknown alias raises KeyError."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo"), tool("srv::get.item")]
    toolset = rig.toolset()
    for spec in toolset.list():
        assert toolset.resolve(spec.name) == spec.fqn
    with pytest.raises(KeyError):
        toolset.resolve("srv__nope")


async def test_call_goes_through_the_invoker_once() -> None:
    """REQ-TLS-001, Art. 3: call() awaits Invoker.call exactly once with the resolved FQN,
    the given arguments and the Toolset's Consumer."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::get.item")]
    toolset = rig.toolset()
    (spec,) = toolset.list()
    outcome = await toolset.call(spec.name, {"text": "hi"})
    assert isinstance(outcome, ToolCallOutcome)
    assert (outcome.is_error, outcome.text) == (False, "ok")
    assert rig.invoker.calls == [(Fqn.parse("srv::get.item"), {"text": "hi"}, HELPER)]
    assert toolset.consumer == HELPER


async def test_call_of_unknown_alias_raises_key_error() -> None:
    """REQ-TLS-002, API §4.12: an alias not in the current listing → KeyError, no call."""
    rig = Rig()
    rig.catalog.entries = [tool("srv::echo")]
    toolset = rig.toolset()
    with pytest.raises(KeyError):
        await toolset.call("srv__nope", {})
    rig.statuses.states["srv"] = ServerState.CONNECTING
    with pytest.raises(KeyError):
        await toolset.call("srv__echo", {})
    assert rig.invoker.calls == []


def test_any_consumer_kind_is_accepted() -> None:
    """REQ-TLS-001, API §4.1: the Toolset carries the Consumer it was built for."""
    rig = Rig()
    assert rig.toolset(Consumer.human()).consumer == Consumer.human()


def test_string_filters_are_rejected() -> None:
    """REQ-TLS-003: a bare string is not a sequence of patterns (it would match per char)."""
    rig = Rig()
    with pytest.raises(TypeError):
        rig.toolset(include="github")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        rig.toolset(exclude="github")  # type: ignore[arg-type]


async def test_host_toolset_is_guarded() -> None:
    """REQ-TLS-001, API §4.1: Host.toolset before start() → RuntimeError, after aclose() →
    HostClosedError."""
    host = Host()
    with pytest.raises(RuntimeError):
        host.toolset(HELPER)
    await host.aclose()
    with pytest.raises(HostClosedError):
        host.toolset(HELPER)
