"""Static checks of the MCP App relay and of the UI code (F7-04; DD-012, delta D4;
REQ-APP-003, REQ-APP-002, Art. 5): the relay is data-only and filters source and origin;
no UI module builds script from message or resource data."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[3] / "src" / "reflex_mcphost" / "ui"
RELAY = UI / "assets" / "mcp_app_relay.js"
FORBIDDEN_JS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "Function(",
    "createContextualFragment",
    "dangerouslySetInnerHTML",
    "srcdoc",
    "import(",
)


def _code(js: str) -> str:
    """The relay without its comments (the header explains what it never does)."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(line.split("//", 1)[0] for line in js.splitlines())


def test_relay_checks_identity_and_origin() -> None:
    """REQ-APP-003, delta D4: a message is accepted only from the proxy window it created
    and only from the proxy origin; posts target the proxy origin, never ``"*"``."""
    code = _code(RELAY.read_text(encoding="utf-8"))
    assert "event.source !== node.contentWindow" in code
    assert "event.origin !== proxyOrigin" in code
    assert "postMessage(entry.message, proxyOrigin)" in code
    assert '"*"' not in code
    assert "MAX_MESSAGE_BYTES = 1048576" in code


def test_relay_forwards_view_navigated_like_any_proxy_message() -> None:
    """REQ-APP-002, REQ-APP-010 (API §7 Lifecycle, delta D7): ``view-navigated`` takes the
    same path as ``sandbox-proxy-ready`` — the relay never reads a message's type or method;
    whatever passed the source and origin checks is forwarded as data, and the backend
    (``ui.apps_bridge``) decides. So a ``view-navigated`` from a sibling frame is dropped by
    the same checks as any forged message."""
    code = _code(RELAY.read_text(encoding="utf-8"))
    assert "view-navigated" not in code
    assert not re.search(r"\.(method|type)\b", code)
    receive = code[code.index("function receive(event)") : code.index("window.addEventListener")]
    checks = receive.index("event.origin !== proxyOrigin")
    assert checks < receive.index("handlers.current.onMessage(viewId, text)")


@pytest.mark.parametrize("needle", FORBIDDEN_JS)
def test_relay_never_turns_data_into_markup_or_script(needle: str) -> None:
    """REQ-APP-002, Art. 5 (delta D4): no HTML sink, no evaluation, no dynamic import."""
    assert needle not in _code(RELAY.read_text(encoding="utf-8"))


def test_relay_has_no_string_timers() -> None:
    """Art. 5: ``setTimeout`` / ``setInterval`` never take a string (here: none at all)."""
    code = _code(RELAY.read_text(encoding="utf-8"))
    assert re.search(r"set(Timeout|Interval)\s*\(", code) is None


def test_relay_iframe_attributes() -> None:
    """REQ-APP-002, REQ-APP-011: the proxy iframe is sandboxed with ``allow-scripts
    allow-same-origin`` and grants no permission (``allow=""``)."""
    code = _code(RELAY.read_text(encoding="utf-8"))
    assert 'sandbox: "allow-scripts allow-same-origin"' in code
    assert 'allow: ""' in code


SCRIPT_BUILDERS = {"call_script", "script", "run_script", "call_function"}


def _script_calls(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in SCRIPT_BUILDERS:
                lines.append(node.lineno)
        if isinstance(node, ast.keyword) and node.arg == "dangerouslySetInnerHTML":
            lines.append(node.value.lineno)
    return lines


def test_no_ui_module_builds_script() -> None:
    """REQ-APP-002, Art. 5 (delta D4): no module of ``reflex_mcphost.ui`` calls
    ``rx.call_script`` / ``rx.script`` / ``run_script`` or binds
    ``dangerouslySetInnerHTML`` — so nothing builds script from message or resource data."""
    found = {}
    for path in sorted(UI.rglob("*.py")):
        lines = _script_calls(ast.parse(path.read_text(encoding="utf-8")))
        if lines:
            found[str(path.relative_to(UI))] = lines
    assert found == {}
