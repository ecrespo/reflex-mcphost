"""ClientLease: ref-counted access to a live Client (F2-07, DD-002)."""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from anyio.lowlevel import checkpoint

from reflex_mcphost.core.connection.lease import ClientLease
from reflex_mcphost.core.errors import ServerUnavailableError

pytestmark = pytest.mark.anyio


class FakeClient:
    pass


async def test_acquire_counts_references() -> None:
    """DD-002: a lease hands out the live client and counts its users."""
    client: Any = FakeClient()
    lease = ClientLease("srv", client)
    assert lease.valid
    async with lease.acquire() as c1:
        assert c1 is client
        async with lease.acquire():
            assert lease.active == 2
        assert lease.active == 1
    assert lease.active == 0
    assert lease.client is client


async def test_use_after_invalidation_raises() -> None:
    """DD-002: once the Client block exited, the lease raises ServerUnavailableError."""
    lease = ClientLease("srv", FakeClient())  # type: ignore[arg-type]
    lease.invalidate("reconnecting")
    assert not lease.valid
    with pytest.raises(ServerUnavailableError) as info:
        _ = lease.client
    assert info.value.server_id == "srv"
    assert info.value.state == "reconnecting"
    with pytest.raises(ServerUnavailableError):
        async with lease.acquire():
            pass


async def test_wait_idle_returns_when_users_leave() -> None:
    """DD-002: the supervisor can wait for in-flight users before exiting the block."""
    lease = ClientLease("srv", FakeClient())  # type: ignore[arg-type]
    await lease.wait_idle()  # no users: immediate
    left = anyio.Event()
    entered = anyio.Event()

    async def user() -> None:
        async with lease.acquire():
            entered.set()
            await left.wait()

    async with anyio.create_task_group() as tg:
        tg.start_soon(user)
        await entered.wait()
        await checkpoint()
        assert lease.active == 1
        left.set()
        with anyio.fail_after(1):
            await lease.wait_idle()
    assert lease.active == 0
