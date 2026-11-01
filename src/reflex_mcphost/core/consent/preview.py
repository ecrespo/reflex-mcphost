"""Argument previews for consent prompts (API §4.7 default preview, REQ-CNS-011/012).

``default_preview`` renders the arguments as pretty JSON, replacing with ``REDACTED`` the
value of every property whose schema says ``format: password`` or ``writeOnly: true``, or
whose name matches ``secret_arg_pattern`` — at any depth, inside objects and arrays. Local
``$ref`` (``#/…``) and ``allOf``/``anyOf``/``oneOf`` branches are followed (a property is
secret if any branch marks it). Redacted locations are listed as dotted paths
(``accounts.0.pin``). Schemas are Server-provided and untrusted: malformed nodes are
ignored, cycles and depth are bounded, and nothing is fetched.

``PreviewRegistry`` holds library hooks (``register_preview``); the most specific target
wins (tool > server > global). A hook that raises, times out or returns something other
than an ``ArgumentPreview`` is logged (type name only) and the default preview is used:
a hook never blocks consent.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

import anyio

from reflex_mcphost.core.catalog import ToolEntry
from reflex_mcphost.core.config.models import DEFAULT_SECRET_ARG_PATTERN
from reflex_mcphost.core.consent.models import ArgumentPreview, ArgumentPreviewHook, ConsentTarget
from reflex_mcphost.core.logging import get_logger

__all__ = ["REDACTED", "PreviewRegistry", "compile_secret_pattern", "default_preview"]

REDACTED = "••••••"
MAX_DEPTH = 64
"""Argument nesting beyond this depth is redacted as a whole (bounded rendering)."""
_MAX_SCHEMA_NODES = 256
_log = get_logger(__name__)

_Nodes = tuple[Mapping[str, Any], ...]


def compile_secret_pattern(pattern: str | re.Pattern[str]) -> re.Pattern[str]:
    if isinstance(pattern, re.Pattern):
        return pattern
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid secret_arg_pattern: {exc}") from None


def _pointer(root: Mapping[str, Any], ref: str) -> Mapping[str, Any] | None:
    if not ref.startswith("#"):
        return None  # remote references are never followed
    node: Any = root
    for raw in ref[1:].split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node if isinstance(node, Mapping) else None


def _expand(nodes: Iterable[Any], root: Mapping[str, Any]) -> _Nodes:
    """``nodes`` plus every subschema reachable via local ``$ref`` and combinators."""
    out: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    stack = [n for n in nodes if isinstance(n, Mapping)]
    while stack and len(out) < _MAX_SCHEMA_NODES:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        out.append(node)
        ref = node.get("$ref")
        if isinstance(ref, str) and (target := _pointer(root, ref)) is not None:
            stack.append(target)
        for combinator in ("allOf", "anyOf", "oneOf"):
            branches = node.get(combinator)
            if isinstance(branches, list):
                stack.extend(b for b in branches if isinstance(b, Mapping))
    return tuple(out)


def _is_secret(nodes: _Nodes) -> bool:
    return any(n.get("format") == "password" or n.get("writeOnly") is True for n in nodes)


def _property_nodes(nodes: _Nodes, name: str) -> Iterator[Any]:
    for node in nodes:
        props = node.get("properties")
        if isinstance(props, Mapping) and name in props:
            yield props[name]
        else:
            yield node.get("additionalProperties")


def _item_nodes(nodes: _Nodes, index: int) -> Iterator[Any]:
    for node in nodes:
        prefix = node.get("prefixItems")
        if isinstance(prefix, list) and index < len(prefix):
            yield prefix[index]
        else:
            yield node.get("items")


class _Redactor:
    def __init__(self, root: Mapping[str, Any], pattern: re.Pattern[str]) -> None:
        self.root = root
        self.pattern = pattern
        self.redacted: list[str] = []

    def _hide(self, path: Sequence[str]) -> str:
        self.redacted.append(".".join(path))
        return REDACTED

    def walk(self, value: Any, nodes: _Nodes, path: tuple[str, ...]) -> Any:
        if len(path) > MAX_DEPTH:
            return self._hide(path)
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for raw_key, child in value.items():
                key = str(raw_key)
                child_nodes = _expand(_property_nodes(nodes, key), self.root)
                child_path = (*path, key)
                if self.pattern.search(key) or _is_secret(child_nodes):
                    out[key] = self._hide(child_path)
                else:
                    out[key] = self.walk(child, child_nodes, child_path)
            return out
        if isinstance(value, list | tuple):
            items: list[Any] = []
            for index, child in enumerate(value):
                child_nodes = _expand(_item_nodes(nodes, index), self.root)
                child_path = (*path, str(index))
                if _is_secret(child_nodes):
                    items.append(self._hide(child_path))
                else:
                    items.append(self.walk(child, child_nodes, child_path))
            return items
        return value


def default_preview(
    arguments: Mapping[str, Any],
    schema: Mapping[str, Any] | None,
    secret_arg_pattern: str | re.Pattern[str] = DEFAULT_SECRET_ARG_PATTERN,
) -> ArgumentPreview:
    """Pretty JSON of ``arguments`` with secret-like values redacted (REQ-CNS-012)."""
    root = schema if isinstance(schema, Mapping) else {}
    redactor = _Redactor(root, compile_secret_pattern(secret_arg_pattern))
    shown = redactor.walk(arguments, _expand([root], root), ())
    body = json.dumps(shown, indent=2, ensure_ascii=False, default=lambda o: type(o).__name__)
    return ArgumentPreview(format="json", body=body, redacted_fields=tuple(redactor.redacted))


def _target_key(target: ConsentTarget) -> tuple[str, str, str]:
    return (target.kind, target.server_id or "", str(target.fqn) if target.fqn else "")


class PreviewRegistry:
    """Preview hooks by target plus the default renderer (REQ-CNS-011)."""

    def __init__(
        self,
        secret_arg_pattern: str | re.Pattern[str] = DEFAULT_SECRET_ARG_PATTERN,
        *,
        hook_timeout_s: float = 5.0,
    ) -> None:
        self._pattern = compile_secret_pattern(secret_arg_pattern)
        self._hook_timeout_s = hook_timeout_s
        self._hooks: dict[tuple[str, str, str], ArgumentPreviewHook] = {}

    @property
    def secret_arg_pattern(self) -> re.Pattern[str]:
        return self._pattern

    @secret_arg_pattern.setter
    def secret_arg_pattern(self, pattern: str | re.Pattern[str]) -> None:
        self._pattern = compile_secret_pattern(pattern)

    def register(self, target: ConsentTarget, hook: ArgumentPreviewHook) -> None:
        """Use ``hook`` for tools covered by ``target``; replaces a hook on the same target."""
        self._hooks[_target_key(target)] = hook

    def _hook_for(self, tool: ToolEntry) -> ArgumentPreviewHook | None:
        for key in (
            ("tool", tool.server_id, str(tool.fqn)),
            ("server", tool.server_id, ""),
            ("global", "", ""),
        ):
            hook = self._hooks.get(key)
            if hook is not None:
                return hook
        return None

    async def render(self, tool: ToolEntry, arguments: Mapping[str, Any]) -> ArgumentPreview:
        """The hook's preview, or the default one when there is no usable hook."""
        hook = self._hook_for(tool)
        if hook is not None:
            try:
                with anyio.fail_after(self._hook_timeout_s):
                    result: Any = hook(tool, copy.deepcopy(dict(arguments)))
                    if inspect.isawaitable(result):
                        result = await result
            except Exception as exc:
                _log.warning(
                    "argument preview hook for %s failed (%s); using the default preview",
                    tool.fqn,
                    type(exc).__name__,
                )
            else:
                if isinstance(result, ArgumentPreview):
                    return result
                _log.warning(
                    "argument preview hook for %s returned %s; using the default preview",
                    tool.fqn,
                    type(result).__name__,
                )
        return default_preview(arguments, tool.input_schema, self._pattern)
