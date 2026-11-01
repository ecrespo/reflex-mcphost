"""``ui.proxy.check_origin_pair``: ``localhost`` resolves only to loopback (F7-03, DD-012).

``localhost`` is resolved with ``AF_INET`` and with ``AF_INET6`` separately (``AF_UNSPEC``
alone can hide ``::1``, spike S-3); every address of every family that resolves must be a
loopback address. The resolver is blocking, so it runs in a worker thread (Art. 8).
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Sequence

import pytest

from reflex_mcphost.ui.proxy.server import LOCALHOST_NOT_LOOPBACK, check_origin_pair

pytestmark = pytest.mark.anyio
V4, V6 = socket.AF_INET, socket.AF_INET6


def _resolver(
    table: dict[socket.AddressFamily, Sequence[str] | Exception],
    calls: list[tuple[str, socket.AddressFamily, int]] | None = None,
) -> Callable[[str, socket.AddressFamily], Sequence[str]]:
    def resolve(host: str, family: socket.AddressFamily) -> Sequence[str]:
        if calls is not None:
            calls.append((host, family, threading.get_ident()))
        answer = table[family]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return resolve


async def test_both_families_loopback_pass() -> None:
    """REQ-APP-002, REQ-APP-007: ``127.0.0.1`` + ``::1`` is a valid origin pair; both
    families are asked for ``localhost``, off the event loop."""
    calls: list[tuple[str, socket.AddressFamily, int]] = []
    resolve = _resolver({V4: ["127.0.0.1"], V6: ["::1"]}, calls)
    assert await check_origin_pair(resolve=resolve) is None
    assert sorted((host, family) for host, family, _ in calls) == [
        ("localhost", V4),
        ("localhost", V6),
    ]
    assert all(ident != threading.get_ident() for _, _, ident in calls)


@pytest.mark.parametrize(
    "table",
    [
        {V4: ["127.0.0.1"], V6: ["2001:db8::1"]},
        {V4: ["127.0.0.1"], V6: ["::1", "fe80::1%eth0"]},
        {V4: ["192.168.1.5"], V6: ["::1"]},
        {V4: ["127.0.0.1", "10.0.0.1"], V6: socket.gaierror(-2, "no")},
    ],
)
async def test_any_non_loopback_address_disables_apps(
    table: dict[socket.AddressFamily, Sequence[str] | Exception],
) -> None:
    """REQ-APP-002, REQ-APP-007, DD-012: a non-loopback address in any family →
    ``localhost_not_loopback``."""
    assert await check_origin_pair(resolve=_resolver(table)) == LOCALHOST_NOT_LOOPBACK


@pytest.mark.parametrize(
    "table",
    [
        {V4: ["127.0.0.1"], V6: socket.gaierror(-9, "no AF_INET6")},
        {V4: OSError("resolver down"), V6: ["::1"]},
        {V4: ["127.0.0.1"], V6: UnicodeError("broken resolver")},
        {V4: ["127.0.0.1"], V6: []},
        {V4: ["127.0.0.2"], V6: ["::1%lo"]},
        {V4: ["127.0.0.1"], V6: ["::ffff:127.0.0.1"]},
    ],
)
async def test_one_family_failing_to_resolve_is_tolerated(
    table: dict[socket.AddressFamily, Sequence[str] | Exception],
) -> None:
    """REQ-APP-002, DD-012: a family that does not resolve is skipped; loopback addresses
    with a scope id or IPv4-mapped loopback pass."""
    assert await check_origin_pair(resolve=_resolver(table)) is None


async def test_nothing_resolving_disables_apps() -> None:
    """REQ-APP-002, DD-012: if neither family resolves, the browser cannot reach the proxy
    either → ``localhost_not_loopback``."""
    table: dict[socket.AddressFamily, Sequence[str] | Exception] = {
        V4: socket.gaierror(-2, "no"),
        V6: [],
    }
    assert await check_origin_pair(resolve=_resolver(table)) == LOCALHOST_NOT_LOOPBACK


async def test_unparsable_address_disables_apps() -> None:
    """REQ-APP-002: an address that is not an IP literal is not loopback."""
    resolve = _resolver({V4: ["not-an-ip"], V6: ["::1"]})
    assert await check_origin_pair(resolve=resolve) == LOCALHOST_NOT_LOOPBACK


async def test_default_resolver_on_this_machine() -> None:
    """REQ-APP-002, REQ-APP-007: the system resolver maps ``localhost`` to loopback here
    (the test machine and the CI runners are configured that way)."""
    assert await check_origin_pair() is None
