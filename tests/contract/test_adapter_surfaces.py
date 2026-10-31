"""Pinned agent-framework surfaces the adapters rely on (spike S-6, API §4.13).

Static items of the S-6 *Contract assertions* are pinned here. Behavioural items (S-6 #7-9,
#13-17 and the extra-missing ImportError) need the adapters themselves and are added with
them in Phase 5 (tasks F5-*). Each block is skipped unless its extra is installed.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing

import pytest


class TestPydanticAI:
    @pytest.fixture(autouse=True)
    def _requires_extra(self) -> None:
        pytest.importorskip("pydantic_ai", reason="extra 'pydantic-ai' not installed")

    def test_imports(self) -> None:
        """S-6 #1: the names the adapter imports exist."""
        from pydantic_ai import RunContext, ToolFailed  # noqa: F401
        from pydantic_ai.tools import ToolDefinition  # noqa: F401
        from pydantic_ai.toolsets import AbstractToolset, ToolsetTool  # noqa: F401

    def test_abstract_toolset(self) -> None:
        """S-6 #2-4: AbstractToolset abstract members and coroutine signatures."""
        from pydantic_ai.toolsets import AbstractToolset

        assert AbstractToolset.__abstractmethods__ == frozenset({"id", "get_tools", "call_tool"})
        assert isinstance(inspect.getattr_static(AbstractToolset, "id"), property)
        get_tools = AbstractToolset.get_tools
        call_tool = AbstractToolset.call_tool
        assert list(inspect.signature(get_tools).parameters) == ["self", "ctx"]
        assert list(inspect.signature(call_tool).parameters) == [
            "self",
            "name",
            "tool_args",
            "ctx",
            "tool",
        ]
        assert inspect.iscoroutinefunction(get_tools)
        assert inspect.iscoroutinefunction(call_tool)

    def test_toolset_tool_and_definition(self) -> None:
        """S-6 #5-6: ToolsetTool keyword-only fields and ToolDefinition fields."""
        from pydantic_ai.tools import ToolDefinition
        from pydantic_ai.toolsets import ToolsetTool

        params = inspect.signature(ToolsetTool).parameters
        for name in ("toolset", "tool_def", "max_retries", "args_validator"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[name].default is inspect.Parameter.empty, name
        assert params["args_validator_func"].default is None
        fields = {f.name for f in dataclasses.fields(ToolDefinition)}
        assert {"name", "parameters_json_schema", "description", "metadata"} <= fields


class TestLangChain:
    @pytest.fixture(autouse=True)
    def _requires_extra(self) -> None:
        pytest.importorskip("langchain_core", reason="extra 'langgraph' not installed")

    def test_base_tool(self) -> None:
        """S-6 #10-11: BaseTool abstract members and the fields the adapter sets."""
        from langchain_core.tools import BaseTool, ToolException  # noqa: F401

        assert BaseTool.__abstractmethods__ == frozenset({"_run"})
        fields = BaseTool.model_fields
        assert {"args_schema", "handle_tool_error", "metadata"} <= set(fields)
        annotation = str(fields["args_schema"].annotation)
        assert "dict[str, typing.Any]" in annotation or "Dict[str, Any]" in annotation

    def test_private_args_hook(self) -> None:
        """S-6 #12: private _to_args_and_kwargs signature the adapter overrides."""
        from langchain_core.tools import BaseTool

        sig = inspect.signature(BaseTool._to_args_and_kwargs)
        assert list(sig.parameters) == ["self", "tool_input", "tool_call_id"]
        assert typing.get_type_hints(BaseTool._to_args_and_kwargs)  # resolvable annotations
