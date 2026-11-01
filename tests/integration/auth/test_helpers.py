"""The auth helpers' stall report: a ``wait_state`` timeout must say where the Host stopped.

An intermittent Windows timeout (``test_issuer_switch_never_reuses_old_credentials
[legacy-configured]``, 2026-11-01) left only "TimeoutError": no state, no log, no request
seen by the fixture AS. The report gives all of that, and never a secret value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import anyio
import anyio.lowlevel
import pytest

from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.connection.stderr import LogRing
from tests.integration.auth import helpers

pytestmark = pytest.mark.anyio


class _StuckSupervisor:
    def __init__(self) -> None:
        self.status = ServerStatus(
            server_id=helpers.SERVER,
            state=ServerState.CONNECTING,
            reason=None,
            since=datetime(2026, 11, 1, tzinfo=UTC),
        )
        self.logs = LogRing(50)
        self.logs.add("server requires authorization", level="warning", logger="host")

    async def wait_for(self, *states: ServerState) -> ServerStatus:
        await anyio.sleep_forever()
        raise AssertionError("unreachable")


async def _parked_request() -> None:
    """Stands in for a Supervisor task stuck on a request nobody answers."""
    await anyio.sleep_forever()


def _host(supervisor: _StuckSupervisor) -> Any:
    return SimpleNamespace(connections=SimpleNamespace(supervisor=lambda _id: supervisor))


async def test_wait_state_timeout_reports_where_the_host_stopped() -> None:
    supervisor = _StuckSupervisor()
    journal = SimpleNamespace(
        journal=[
            {"endpoint": "metadata", "issuer": "http://as/t2", "params": [], "method": "GET"},
            {"endpoint": "introspect", "issuer": "http://as", "params": ["token"]},
        ]
    )
    async with anyio.create_task_group() as tg:
        tg.start_soon(_parked_request, name="supervisor:remote")
        await anyio.lowlevel.checkpoint()
        with pytest.raises(AssertionError) as caught:
            await helpers.wait_state(
                _host(supervisor), ServerState.AUTH_REQUIRED, oauth_as=journal, limit_s=0.05
            )
        tg.cancel_scope.cancel()
    report = str(caught.value)
    assert "waited 0.05 s for auth_required" in report
    assert "state=connecting" in report
    assert "server requires authorization" in report  # the Host log tail
    assert "metadata GET http://as/t2" in report  # the AS journal, endpoints only
    assert "introspect" in report
    assert "pending tasks:" in report
    parked = [line for line in report.splitlines() if "supervisor:remote:" in line]
    assert len(parked) == 1
    assert "test_helpers.py" in parked[0]  # the other task's own waiting point
    assert "_parked_request" in parked[0]


def test_stall_report_never_carries_journal_values() -> None:
    """The journal keeps parameter *names*; the report prints endpoints only."""
    secret = "report-secret-value"  # noqa: S105 - test sentinel
    journal = SimpleNamespace(
        journal=[{"endpoint": "token", "issuer": "http://as", "params": ["code"], "x": secret}]
    )
    report = helpers.stall_report(_StuckSupervisor(), (ServerState.READY,), journal, 1.0)
    assert secret not in report
    assert "token" in report
