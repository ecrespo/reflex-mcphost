"""Argument previews and redaction (F3-05, API §4.7 default preview)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import anyio
import anyio.lowlevel
import pytest

from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.config.models import DEFAULT_SECRET_ARG_PATTERN
from reflex_mcphost.core.consent.models import ArgumentPreview, ConsentTarget
from reflex_mcphost.core.consent.preview import REDACTED, PreviewRegistry, default_preview
from tests.unit.core.consent.factories import tool_entry

pytestmark = pytest.mark.anyio

SECRETS = ("hunter2-pw", "wo-secret-value", "tok-123456", "nested-key-987", "arr-pass-55")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "login": {"type": "string"},
        "pw": {"type": "string", "format": "password"},
        "blob": {"type": "string", "writeOnly": True},
        "config": {
            "type": "object",
            "properties": {"inner": {"type": "object", "properties": {"x": {"type": "integer"}}}},
        },
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"user": {"type": "string"}, "pin": {"$ref": "#/$defs/secret"}},
            },
        },
        "choice": {"anyOf": [{"type": "string", "format": "password"}, {"type": "integer"}]},
    },
    "$defs": {"secret": {"type": "string", "writeOnly": True}},
}
ARGS: dict[str, Any] = {
    "login": "ernesto",
    "pw": "hunter2-pw",
    "blob": "wo-secret-value",
    "auth_token": "tok-123456",
    "config": {"inner": {"x": 1, "ApiKey": "nested-key-987"}},
    "accounts": [{"user": "a", "pin": "1111"}, {"user": "b", "Password": "arr-pass-55"}],
    "choice": "choice-secret",
}


def test_default_preview_redacts_secret_like_values() -> None:
    """REQ-CNS-012: format password, writeOnly and secret-pattern names are redacted, nested."""
    preview = default_preview(ARGS, SCHEMA, DEFAULT_SECRET_ARG_PATTERN)
    assert preview.format == "json"
    for secret in (*SECRETS, "1111", "choice-secret"):
        assert secret not in preview.body
    body = json.loads(preview.body)
    assert body["login"] == "ernesto"
    assert body["pw"] == REDACTED
    assert body["config"]["inner"] == {"x": 1, "ApiKey": REDACTED}
    assert body["accounts"][0] == {"user": "a", "pin": REDACTED}
    assert set(preview.redacted_fields) == {
        "pw",
        "blob",
        "auth_token",
        "config.inner.ApiKey",
        "accounts.0.pin",
        "accounts.1.Password",
        "choice",
    }
    assert preview.body == json.dumps(body, indent=2, ensure_ascii=False)


def test_secret_container_is_replaced_whole() -> None:
    """REQ-CNS-012: a secret-named object is replaced as a whole."""
    preview = default_preview({"credentials": {"user": "u", "x": "zz-1"}}, None, "credential")
    assert json.loads(preview.body) == {"credentials": REDACTED}
    assert preview.redacted_fields == ("credentials",)


def test_custom_pattern_and_no_schema() -> None:
    preview = default_preview({"pin": "4321", "name": "n"}, None, r"(?i)^pin$")
    assert json.loads(preview.body) == {"pin": REDACTED, "name": "n"}


def test_pathological_schema_does_not_break_preview() -> None:
    """REQ-CNS-012: cyclic $ref, non-dict nodes and odd values still yield a preview."""
    schema = {
        "properties": {"a": {"$ref": "#/properties/a"}, "b": True, "c": {"$ref": "http://x/y"}},
        "additionalProperties": {"format": "password"},
        "items": "bogus",
    }
    preview = default_preview({"a": {"b": 1}, "b": 2, "c": 3, "extra": "e-secret"}, schema, "zzz")
    body = json.loads(preview.body)
    assert body["extra"] == REDACTED
    assert body["b"] == 2


async def test_hook_specificity_and_async() -> None:
    """REQ-CNS-011: the most specific registered hook replaces the default preview."""
    registry = PreviewRegistry(DEFAULT_SECRET_ARG_PATTERN)
    tool = tool_entry("srv::echo")

    def global_hook(_: ToolEntry, __: Mapping[str, Any]) -> ArgumentPreview:
        return ArgumentPreview(format="text", body="global")

    async def server_hook(_: ToolEntry, __: Mapping[str, Any]) -> ArgumentPreview:
        await anyio.lowlevel.checkpoint()
        return ArgumentPreview(format="markdown", body="server")

    assert (await registry.render(tool, {"pw": "x"})).format == "json"
    registry.register(ConsentTarget.global_(), global_hook)
    assert (await registry.render(tool, {})).body == "global"
    registry.register(ConsentTarget.server("srv"), server_hook)
    assert (await registry.render(tool, {})).body == "server"
    assert (await registry.render(tool_entry("other::x"), {})).body == "global"
    registry.register(
        ConsentTarget.tool("srv::echo"), lambda _t, a: ArgumentPreview(body=str(sorted(a)))
    )
    assert (await registry.render(tool, {"k": 1})).body == "['k']"
    assert (await registry.render(tool_entry("srv::other"), {})).body == "server"


@pytest.mark.parametrize("mode", ["raise", "wrong_type", "async_raise"])
async def test_failing_hook_falls_back_to_default(
    mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-CNS-011, REQ-CNS-012: a failing hook never blocks consent; default preview used."""
    registry = PreviewRegistry(DEFAULT_SECRET_ARG_PATTERN)

    def bad(_: ToolEntry, args: Mapping[str, Any]) -> Any:
        if mode == "raise":
            raise RuntimeError(f"boom {args['pw']}")
        return {"not": "a preview"}

    async def bad_async(_: ToolEntry, args: Mapping[str, Any]) -> ArgumentPreview:
        raise RuntimeError(f"boom {args['pw']}")

    registry.register(ConsentTarget.server("srv"), bad_async if mode == "async_raise" else bad)
    tool = tool_entry("srv::echo", input_schema=SCHEMA)
    with caplog.at_level(logging.WARNING, logger="reflex_mcphost"):
        preview = await registry.render(tool, ARGS)
    assert preview == default_preview(ARGS, SCHEMA, DEFAULT_SECRET_ARG_PATTERN)
    assert "preview hook" in caplog.text
    assert "hunter2-pw" not in caplog.text


async def test_hanging_hook_times_out() -> None:
    """REQ-CNS-011: a hook that does not return in time falls back to the default."""
    registry = PreviewRegistry(DEFAULT_SECRET_ARG_PATTERN, hook_timeout_s=0.05)

    async def slow(_: ToolEntry, __: Mapping[str, Any]) -> ArgumentPreview:
        await anyio.sleep(10)
        raise AssertionError("unreachable")

    registry.register(ConsentTarget.global_(), slow)
    preview = await registry.render(tool_entry(), {"a": 1})
    assert json.loads(preview.body) == {"a": 1}


def test_invalid_pattern_rejected() -> None:
    with pytest.raises(ValueError, match="secret_arg_pattern"):
        PreviewRegistry("(unclosed")
