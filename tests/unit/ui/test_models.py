"""``ui.models``: JSON-serializable view models, no Reflex (F6-02; TD §5.1, delta D7)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime

import pytest
from mcp_types import Implementation, ServerCapabilities

from reflex_mcphost.core.audit import AuditRecord
from reflex_mcphost.core.catalog import PromptEntry, ResourceEntry, ResourceTemplateEntry
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.connection.stderr import LogEntry
from reflex_mcphost.core.consent.models import ConsentTarget
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.registry import ServerRegistry
from reflex_mcphost.core.types import Consumer, Fqn
from reflex_mcphost.ui import models
from tests.integration.test_host import MODERN_TABLE, Setup, setup
from tests.unit.core.consent.factories import rule, tool_entry

pytestmark = pytest.mark.anyio
__all__ = ["setup"]  # re-exported fixture
ENV_SECRET = "models-env-secret-0101"  # noqa: S105 - test sentinel
HEADER_SECRET = "models-header-secret-0102"  # noqa: S105 - test sentinel
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def _status(server_id: str, state: ServerState = ServerState.READY, **kw: object) -> ServerStatus:
    return ServerStatus(server_id=server_id, state=state, since=NOW, **kw)  # type: ignore[arg-type]


async def _registry(setup: Setup) -> ServerRegistry:
    setup.env.update(SEEDED_ENV=ENV_SECRET, SEEDED_HEADER=HEADER_SECRET)
    tables = {
        "local": MODERN_TABLE + '\nenv = { API_TOKEN = "${SEEDED_ENV}" }',
        "remote": "\n".join(
            [
                'transport = "http"',
                'url = "https://mcp.example.com/mcp?x=1"',
                'headers = { Authorization = "${SEEDED_HEADER}" }',
            ]
        ),
        "broken": MODERN_TABLE + '\nenv = { DSN = "${DB_URL}" }',
    }
    registry = ServerRegistry(setup.config(tables), platform=setup.files.platform, env=setup.env)
    await registry.load()
    return registry


async def test_server_rows_carry_keys_never_values(setup: Setup) -> None:
    """REQ-REG-007, REQ-NFR-010, REQ-UI-003 (row columns): rows name env and header keys
    only; a seeded env value and header value appear in no row; HTTP rows show the
    destination (no path or query); a ``config_error`` row names ``DB_URL`` by key."""
    registry = await _registry(setup)
    caps = ServerCapabilities(tools={}, logging={})
    statuses = [
        _status(
            "local",
            protocol_version="2026-07-28",
            era="modern",
            capabilities=caps,
            server_info=Implementation(name="fixture", version="1"),
        ),
        _status("remote", ServerState.AUTH_REQUIRED, reason="auth_required"),
        _status("broken", ServerState.CONFIG_ERROR, reason="config_error"),
    ]
    rows = models.server_rows(statuses, registry)
    text = json.dumps(rows)
    assert ENV_SECRET not in text
    assert HEADER_SECRET not in text
    by_id = {r["id"]: r for r in rows}
    local = by_id["local"]
    assert local["env_keys"] == ["API_TOKEN"]
    assert local["state"] == "ready"
    assert local["era"] == "modern"
    assert local["protocol_version"] == "2026-07-28"
    assert local["capabilities"] == ["logging", "tools"]
    assert local["transport"] == "stdio"
    assert local["origin_kind"] == "mcphost_user"
    assert local["enabled"] is True
    assert local["server_name"] == "fixture"
    remote = by_id["remote"]
    assert remote["header_keys"] == ["Authorization"]
    assert remote["destination"] == "https://mcp.example.com"
    assert remote["reason"] == "auth_required"
    broken = by_id["broken"]
    assert broken["state"] == "config_error"
    assert any("DB_URL" in p for p in broken["problems"])


async def test_server_rows_without_a_registry_and_shadowed(setup: Setup) -> None:
    """REQ-UI-003, REQ-REG-003: a row without a definition still shows its state; shadowed
    definitions are listed by origin."""
    rows = models.server_rows([_status("ghost", ServerState.DISCONNECTED)], None)
    assert rows[0]["id"] == "ghost"
    assert rows[0]["transport"] is None
    setup.files.install_at(
        setup.files.cwd / ".mcp.json",
        text=json.dumps({"mcpServers": {"local": {"command": "python", "args": []}}}),
    )
    registry = await _registry(setup)
    row = models.server_row(_status("local"), registry)
    assert row["shadowed"] == [
        {"origin_kind": "mcp_json_project", "origin_path": str(setup.files.cwd / ".mcp.json")}
    ]


def test_catalog_rows_per_list() -> None:
    """REQ-UI-004 (catalog view model): every list maps to rows; descriptions are kept as
    plain text."""
    tool = tool_entry("srv::echo", description="<b>Echo</b> **bold**")
    tools = models.catalog_rows("tools", [tool])
    assert tools == [
        {
            "id": "srv::echo",
            "fqn": "srv::echo",
            "server_id": "srv",
            "name": "echo",
            "title": None,
            "description": "<b>Echo</b> **bold**",
            "stale": False,
            "has_ui": False,
            "read_only": None,
            "destructive": None,
        }
    ]
    resource = ResourceEntry(server_id="srv", uri="file:///a", name="a")
    template = ResourceTemplateEntry(
        fqn=Fqn.parse("srv::t"), server_id="srv", name="t", uri_template="file:///{x}"
    )
    prompt = PromptEntry(fqn=Fqn.parse("srv::p"), server_id="srv", name="p")
    assert models.catalog_rows("resources", [resource])[0]["id"] == "file:///a"
    assert models.catalog_rows("resource_templates", [template])[0]["uri_template"] == (
        "file:///{x}"
    )
    assert models.catalog_rows("prompts", [prompt])[0]["arguments"] == []
    json.dumps([tools, models.catalog_rows("prompts", [prompt])])


def test_rule_audit_and_log_rows() -> None:
    """REQ-UI-006 (view models): rules and audit records map to JSON rows."""
    stored = rule("deny", target=ConsentTarget.server("srv"), consumer=Consumer.agent("bot"))
    rows = models.rule_rows([stored])
    assert rows[0]["id"] == stored.id
    assert rows[0]["target"] == "server srv"
    assert rows[0]["consumer"] == "agent:bot"
    record = AuditRecord(
        invocation_id="inv_1",
        operation=OperationKind.TOOL_CALL,
        timestamp=NOW,
        consumer=Consumer.human(),
        server_id="srv",
        fqn=Fqn.parse("srv::echo"),
        args_hash="sha256:00",
        decision_source="prompt",
        rule_id=None,
        duration_ms=3,
        outcome=InvocationOutcome.success,
        error_code=None,
        input_rounds=0,
        attribution="exact",
    )
    audit = models.audit_rows([record])
    assert audit[0]["consumer"] == "human:ui"
    assert audit[0]["outcome"] == "success"
    assert "args_hash" not in audit[0]
    log = models.log_rows([LogEntry(level="info", logger="stderr", timestamp=NOW, text="hi")])
    assert log[0]["text"] == "hi"
    json.dumps([rows, audit, log])


def test_page_bounds_rows_and_reports_the_rest() -> None:
    """REQ-NFR-004 (bounded rendering logic): ``page`` keeps at most 200 rows and reports
    how many more matched; matching is a case-insensitive substring over id / FQN / title /
    description."""
    rows = [
        {"id": f"srv::tool{i}", "fqn": f"srv::tool{i}", "title": None, "description": "x"}
        for i in range(450)
    ]
    shown, more = models.page(rows, "")
    assert len(shown) == 200
    assert more == 250
    shown, more = models.page(rows, "TOOL44")
    assert [r["id"] for r in shown] == [f"srv::tool{i}" for i in (44, *range(440, 450))]
    assert more == 0
    titled = [{"id": "a", "title": "Weather Lookup", "description": None}]
    assert models.page(titled, "weather")[0] == titled
    assert models.page(titled, "nope") == ([], 0)
    assert len(models.page(rows, "", limit=10)[0]) == 10


def test_filtering_two_thousand_rows_is_fast() -> None:
    """REQ-NFR-004 (informational): filtering 2,000 rows takes < 20 ms in pure Python."""
    rows = [
        {"id": f"s{i % 50}::t{i}", "fqn": f"s{i % 50}::t{i}", "title": f"Tool {i}",
         "description": "lorem ipsum dolor sit amet " * 4}
        for i in range(2000)
    ]  # fmt: skip
    started = time.perf_counter()
    for query in ("t19", "ipsum", "zzz"):
        models.page(rows, query)
    assert (time.perf_counter() - started) / 3 < 0.020


def test_replace_rows_keeps_order() -> None:
    """REQ-NFR-004: one Server's rows are replaced in place; others are untouched."""
    rows = [{"server_id": "a", "id": "a::1"}, {"server_id": "b", "id": "b::1"}]
    new = models.replace_server_rows(rows, "a", [{"server_id": "a", "id": "a::2"}])
    assert [r["id"] for r in new] == ["a::2", "b::1"]
    assert [r["id"] for r in models.replace_server_rows(new, "b", [])] == ["a::2"]
    added = models.replace_server_rows(new, "c", [{"server_id": "c", "id": "c::1"}])
    assert [r["id"] for r in added] == ["a::2", "b::1", "c::1"]


def test_models_and_the_ui_package_import_without_reflex() -> None:
    """REQ-UI-007, TD §5.1 (delta D7): ``reflex_mcphost.ui`` and ``ui.models`` import no
    Reflex; the Reflex names of API §8 resolve lazily."""
    code = (
        "import sys, reflex_mcphost.ui as ui, reflex_mcphost.ui.models\n"
        "assert 'reflex' not in sys.modules, 'reflex imported'\n"
        "assert callable(ui.install) and ui.HostRuntime.phase == 'stopped'\n"
        "assert callable(ui.consent_center) and 'reflex' in sys.modules\n"
        "assert sorted(dir(ui)) == sorted(ui.__all__)\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=120
    )
    assert result.returncode == 0, result.stderr
