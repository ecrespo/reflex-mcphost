"""PydanticAI adapter (extra ``pydantic-ai``; REQ-TLS-006, API §4.13, spike S-6).

``as_pydantic_ai_toolset(toolset)`` returns a ``HostToolset``, a subclass of PydanticAI's
own ``AbstractToolset`` (Art. 2). PydanticAI calls ``get_tools`` before every model request
and the adapter re-reads ``Toolset.list()`` each time, so catalog changes show up within a
run (REQ-TLS-004). Arguments are accepted as any JSON object: the Host's Invoker validates
them against the tool's input schema (REQ-INV-002), as PydanticAI's own ``MCPToolset`` leaves
validation to the server. Every error outcome — and an alias that vanished since the
listing — is raised as ``ToolFailed(text)``, which PydanticAI returns to the model as a
failed tool result without spending the retry budget (REQ-TLS-005). Effects go only
through ``Toolset.call`` (Art. 3).
"""

from __future__ import annotations

from typing import Any

try:
    import pydantic_core
    from pydantic_ai import RunContext, ToolFailed
    from pydantic_ai.tools import ToolDefinition
    from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
except ImportError as exc:  # pragma: no cover - exercised in a subprocess (F5-05)
    raise ImportError(
        "reflex_mcphost.adapters.pydantic_ai needs the 'pydantic-ai' extra: "
        "pip install 'reflex-mcphost[pydantic-ai]'"
    ) from exc

from reflex_mcphost.toolset import Toolset

__all__ = ["as_pydantic_ai_toolset"]

_ANY_OBJECT = pydantic_core.SchemaValidator(
    pydantic_core.core_schema.dict_schema(
        pydantic_core.core_schema.str_schema(), pydantic_core.core_schema.any_schema()
    )
)
"""Accept any JSON object; the Invoker validates against the tool's input schema."""


class HostToolset(AbstractToolset[Any]):
    """The Host's tools as a PydanticAI toolset (one per ``Toolset``)."""

    def __init__(self, toolset: Toolset) -> None:
        self._toolset = toolset

    @property
    def id(self) -> str | None:
        """No id: durable execution (which needs one) is out of scope (API §4.13)."""
        return None

    @property
    def toolset(self) -> Toolset:
        """The wrapped framework-neutral Toolset."""
        return self._toolset

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        """The current listing, re-read on every run step (REQ-TLS-004)."""
        return {
            spec.name: ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=spec.name,
                    description=spec.description,
                    parameters_json_schema=spec.input_schema,
                    metadata={"fqn": str(spec.fqn)},
                ),
                max_retries=1,
                args_validator=_ANY_OBJECT,
            )
            for spec in self._toolset.list()
        }

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        """Invoke through ``Toolset.call``; error outcomes raise ``ToolFailed(text)``."""
        try:
            outcome = await self._toolset.call(name, tool_args)
        except KeyError:
            raise ToolFailed(f"Tool is no longer available: {name}") from None
        if outcome.is_error:
            raise ToolFailed(outcome.text)
        return outcome.text


def as_pydantic_ai_toolset(toolset: Toolset) -> AbstractToolset[Any]:
    """Expose ``toolset`` as a PydanticAI toolset: ``Agent(model, toolsets=[it])``."""
    return HostToolset(toolset)
