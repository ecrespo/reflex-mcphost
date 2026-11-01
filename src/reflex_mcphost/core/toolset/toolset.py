"""``Toolset``: tools of ready Servers as agent-facing specs (API §4.12, RF-TLS).

The Toolset is a view, never a second effect path (Art. 3): ``list()`` reads the live
catalog on every call (REQ-TLS-004) and keeps the tools of ``ready`` Servers that are not
stale, are model-visible (REQ-APP-008) and pass the filters (REQ-TLS-003); aliases are
assigned over exactly that listing (REQ-TLS-002). ``call()`` resolves the alias against a
fresh listing and hands the FQN to ``Invoker.call`` under the Toolset's Consumer —
consent, validation, deadlines, MRTR and audit all stay in the Invoker. Every invocation
outcome comes back as a ``ToolCallOutcome`` (REQ-TLS-005); the text is built from the
Server's own output and the Invoker's bounded, redacted error message, never from argument
values, and only alias, FQN and outcome are logged (Art. 5).
"""

from __future__ import annotations

import builtins
import copy
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel

from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.connection.state import ServerState, ServerStatus
from reflex_mcphost.core.errors import UnknownServerError, UnknownToolError
from reflex_mcphost.core.invoker import InvocationResult
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.outcomes import InvocationOutcome
from reflex_mcphost.core.toolset.aliases import assign_aliases
from reflex_mcphost.core.toolset.filters import check_patterns, selected
from reflex_mcphost.core.types import Consumer, Fqn

__all__ = ["ToolCallOutcome", "ToolSpec", "Toolset", "outcome_of"]

_log = get_logger(__name__)
DENIED_PREFIX = "Permission denied by the host: "
TOOL_ERROR_DEFAULT = "The tool reported an error"


class ToolSpec(BaseModel, frozen=True):
    """One agent-facing tool (API §4.12). Schemas are private copies per listing."""

    name: str
    """Alias matching ``^[A-Za-z0-9_-]{1,64}$`` (REQ-TLS-002)."""
    fqn: Fqn
    description: str
    """``"[server_id] "`` + the tool's description (or title, or name)."""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


class ToolCallOutcome(BaseModel, frozen=True):
    """What an agent sees of one call (API §4.12, delta D3)."""

    is_error: bool
    """``True`` iff the invocation outcome is not ``success``."""
    text: str
    structured_content: dict[str, Any] | None = None
    result: InvocationResult


class ToolSource(Protocol):
    """The catalog surface the Toolset reads (``Catalog.tools``)."""

    def tools(self, server_id: str | None = None) -> list[ToolEntry]: ...


class ToolInvoker(Protocol):
    """The Invoker surface the Toolset calls (``Invoker.call``)."""

    async def call(
        self, fqn: Fqn | str, arguments: Mapping[str, Any] | None, *, consumer: Consumer
    ) -> InvocationResult: ...


def _agent_text(result: InvocationResult) -> str:
    outcome = result.outcome
    message = result.error.message if result.error is not None else ""
    if outcome is InvocationOutcome.success:
        text = result.text
        if not text and result.structured_content is not None:
            return json.dumps(result.structured_content, separators=(",", ":"), ensure_ascii=False)
        return text
    if outcome is InvocationOutcome.tool_error:
        return result.text or TOOL_ERROR_DEFAULT
    if outcome is InvocationOutcome.denied:
        return DENIED_PREFIX + (message or "denied")
    return f"Tool call failed ({outcome.value}): {message or outcome.value}"


def outcome_of(result: InvocationResult) -> ToolCallOutcome:
    """Map an ``InvocationResult`` to the agent-facing outcome (API §4.12 text table)."""
    return ToolCallOutcome(
        is_error=result.outcome is not InvocationOutcome.success,
        text=_agent_text(result),
        structured_content=result.structured_content,
        result=result,
    )


def _description(entry: ToolEntry) -> str:
    return f"[{entry.server_id}] {entry.description or entry.title or entry.name}"


class Toolset:
    """Framework-neutral agent bridge over one Host (API §4.12); built by ``Host.toolset``.

    ``status_of`` is ``ConnectionManager.status`` and ``invoker_of`` returns the Host's
    Invoker on each call, so the Host's lifecycle guards (``RuntimeError`` before
    ``start()``, ``HostClosedError`` after ``aclose()``) apply to every ``call()``.
    """

    def __init__(
        self,
        *,
        catalog: ToolSource,
        status_of: Callable[[str], ServerStatus],
        invoker_of: Callable[[], ToolInvoker],
        consumer: Consumer,
        include: Sequence[str] = ("*",),
        exclude: Sequence[str] = (),
    ) -> None:
        self._catalog = catalog
        self._status_of = status_of
        self._invoker_of = invoker_of
        self.consumer = consumer
        """The Consumer every call is evaluated and audited for (REQ-CNS-015)."""
        self.include = check_patterns("include", include)
        self.exclude = check_patterns("exclude", exclude)

    def _listing(self) -> dict[str, ToolEntry]:
        ready: dict[str, bool] = {}

        def is_ready(server_id: str) -> bool:
            if server_id not in ready:
                try:
                    ready[server_id] = self._status_of(server_id).state is ServerState.READY
                except UnknownServerError:  # left the registry; catalog not dropped yet
                    ready[server_id] = False
            return ready[server_id]

        entries = [
            e
            for e in self._catalog.tools()
            if not e.stale
            and "model" in e.visibility
            and selected(e.fqn, self.include, self.exclude)
            and is_ready(e.server_id)
        ]
        aliases = assign_aliases(e.fqn for e in entries)
        return {aliases[e.fqn]: e for e in entries}

    def list(self) -> builtins.list[ToolSpec]:
        """The current tools, in catalog order (sync; REQ-TLS-001, REQ-TLS-004)."""
        return [
            ToolSpec(
                name=alias,
                fqn=entry.fqn,
                description=_description(entry),
                input_schema=copy.deepcopy(entry.input_schema),
                output_schema=copy.deepcopy(entry.output_schema),
            )
            for alias, entry in self._listing().items()
        ]

    def resolve(self, name: str) -> Fqn:
        """The FQN behind an alias of the current listing; ``UnknownToolError`` (a
        ``KeyError``) if absent."""
        try:
            return self._listing()[name].fqn
        except KeyError:
            raise UnknownToolError(name) from None

    async def call(self, name: str, arguments: Mapping[str, Any] | None) -> ToolCallOutcome:
        """Invoke the tool behind ``name`` through the Invoker (REQ-TLS-001, REQ-TLS-005).

        Never raises for invocation outcomes. Raises ``UnknownToolError`` (also a
        ``KeyError``) for an alias not in the current listing, the Host's ``RuntimeError`` /
        ``HostClosedError`` misuse errors, and propagates the cancellation of the awaiting
        task.
        """
        invoker = self._invoker_of()
        fqn = self.resolve(name)
        result = await invoker.call(fqn, arguments, consumer=self.consumer)
        _log.debug(
            "toolset call %s -> %s: %s",
            name,
            fqn,
            result.outcome.value,
            extra={"fqn": str(fqn), "invocation_id": result.id},
        )
        return outcome_of(result)
