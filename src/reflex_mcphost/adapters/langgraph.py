"""LangChain/LangGraph adapter (extra ``langgraph``; REQ-TLS-007, API §4.13, spike S-6).

``as_langchain_tools(toolset)`` returns one ``HostTool`` per ``Toolset.list()`` entry. The
tools are a snapshot: call it again after ``catalog.changed`` (API §4.13). ``HostTool`` is a
direct ``langchain_core.tools.BaseTool`` subclass (Art. 2) — not ``StructuredTool``, which
drops or overrides arguments named ``config``/``run_manager`` and fails on ``self`` (S-6):
the parsed argument dict is passed to ``_arun`` as one positional argument and ``_arun``
declares neither ``config`` nor ``run_manager``, so LangChain injects nothing. The dict
``args_schema`` is the Server's input schema unchanged; LangChain does not validate it,
the Host's Invoker does (REQ-INV-002). Every error outcome, and an alias gone since the
snapshot, is raised as ``ToolException(text)`` and, with ``handle_tool_error=True``, becomes
``ToolMessage(status="error")`` — LangGraph's ``ToolNode`` re-raises any other exception and
fails the graph (REQ-TLS-005). Only ``langchain_core`` is imported; effects go only through
``Toolset.call`` (Art. 3).
"""

from __future__ import annotations

from typing import Any

try:
    from langchain_core.tools import BaseTool, ToolException
except ImportError as exc:  # pragma: no cover - exercised in a subprocess (F5-05)
    raise ImportError(
        "reflex_mcphost.adapters.langgraph needs the 'langgraph' extra: "
        "pip install 'reflex-mcphost[langgraph]'"
    ) from exc

from pydantic import Field

from reflex_mcphost.toolset import Toolset

__all__ = ["as_langchain_tools"]

ASYNC_ONLY = "reflex-mcphost tools are async-only; use ainvoke"


class HostTool(BaseTool):
    """One Host tool as a LangChain tool (async-only)."""

    toolset: Toolset = Field(exclude=True)
    handle_tool_error: Any = True

    def _to_args_and_kwargs(
        self, tool_input: str | dict[str, Any], tool_call_id: str | None
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        parsed = self._parse_input(tool_input, tool_call_id)
        if not isinstance(parsed, dict):  # a str input is rejected by _parse_input already
            raise ToolException(f"{self.name} takes a JSON object of arguments")
        return (dict(parsed),), {}

    def _run(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError(ASYNC_ONLY)

    async def _arun(self, arguments: dict[str, Any]) -> str:
        try:
            outcome = await self.toolset.call(self.name, arguments)
        except KeyError:
            raise ToolException(f"Tool is no longer available: {self.name}") from None
        if outcome.is_error:
            raise ToolException(outcome.text)
        return outcome.text


def as_langchain_tools(toolset: Toolset) -> list[BaseTool]:
    """Snapshot ``toolset`` as LangChain tools (e.g. for ``langgraph.prebuilt.ToolNode``)."""
    return [
        HostTool(
            name=spec.name,
            description=spec.description,
            args_schema=spec.input_schema,
            metadata={"fqn": str(spec.fqn)},
            toolset=toolset,
        )
        for spec in toolset.list()
    ]
