"""The Host UI's actions as plain async functions (F6-04, F6-05, F6-07; Reflex-free).

``HostState`` event handlers are thin wrappers around these, so every action is tested
with a fake Host and no Reflex runtime. Each action calls public core methods only:

- Servers (REQ-UI-003): ``connections.connect / disconnect / reconnect / sign_out /
  set_log_level / authorize``, ``registry.set_enabled / reload`` — exactly one call per
  action, with the row's id.
- Catalog (REQ-UI-004): ``invoker.start(..., consumer=Consumer.human())`` (Art. 3: the UI
  invokes only through the Invoker), ``InvocationHandle.progress / cancel / result``,
  ``catalog.read_resource / read_template / get_prompt``.
- Permissions / Audit (REQ-UI-006): ``consent.rules / update / revoke``, ``audit.query``.

Notices are short status lines for the view. They never carry an authorization URL (it
carries ``state``, REQ-AUTH-008), tokens, codes or argument values; error texts come from
core exceptions, whose messages are already bounded and redacted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from reflex_mcphost.core.errors import AuthorizationError, MCPHostError
from reflex_mcphost.core.logging import get_logger
from reflex_mcphost.core.types import Consumer
from reflex_mcphost.ui import models

__all__ = [
    "INVOCATIONS",
    "SERVER_ACTIONS",
    "Commit",
    "audit_page",
    "authorization_outcome",
    "cancel_invocation",
    "get_prompt",
    "invoke",
    "read_resource",
    "read_template",
    "refresh_audit",
    "reload_servers",
    "revoke_rule",
    "rule_view",
    "server_action",
    "set_log_level",
    "start_authorization",
    "update_rule",
]

Commit = Callable[[dict[str, Any]], Awaitable[None]]
HUMAN = Consumer.human()
SERVER_ACTIONS = ("connect", "disconnect", "reconnect", "sign_out", "enable", "disable")
"""Per-row Server actions (authorize, reload and log level have their own functions)."""
LABELS = {
    "connect": "Connect",
    "disconnect": "Disconnect",
    "reconnect": "Reconnect",
    "sign_out": "Sign out",
    "enable": "Enable",
    "disable": "Disable",
}
INVOCATIONS: dict[str, Any] = {}
"""Running invocation handle per client token (process-local, never in Reflex state)."""
_log = get_logger(__name__)


def _failed(what: str, exc: Exception) -> str:
    if isinstance(exc, AuthorizationError):
        return f"{what} failed: {exc.reason}"
    return f"{what} failed: {exc}"


# ------------------------------------------------------------------ Servers (F6-04)


async def server_action(host: Any, action: str, server_id: str) -> str:
    """Run one Servers row action; the notice to show."""
    connections = host.connections
    calls: dict[str, Callable[[], Awaitable[Any]]] = {
        "connect": lambda: connections.connect(server_id),
        "disconnect": lambda: connections.disconnect(server_id),
        "reconnect": lambda: connections.reconnect(server_id),
        "sign_out": lambda: connections.sign_out(server_id),
        "enable": lambda: host.registry.set_enabled(server_id, True),
        "disable": lambda: host.registry.set_enabled(server_id, False),
    }
    call = calls.get(action)
    if call is None:
        return f"unknown action {action!r}"
    label = f"{LABELS[action]} {server_id}"
    try:
        await call()
    except (MCPHostError, ValueError) as exc:
        return _failed(label, exc)
    return f"{label}: done"


async def reload_servers(host: Any) -> str:
    """Re-read every config source (REQ-REG-013)."""
    try:
        diff = await host.registry.reload()
    except (MCPHostError, ValueError) as exc:
        return _failed("Reload", exc)
    return (
        f"Reloaded: {len(diff.added)} added, {len(diff.removed)} removed, "
        f"{len(diff.changed)} changed"
    )


async def set_log_level(host: Any, server_id: str, level: str) -> str:
    """*Deprecated (SEP-2577), compatibility* (REQ-HSH-031)."""
    try:
        await host.connections.set_log_level(server_id, level)
    except (MCPHostError, ValueError) as exc:
        return _failed(f"Log level for {server_id}", exc)
    return f"Log level for {server_id}: {level}"


async def start_authorization(host: Any, server_id: str) -> tuple[Any, str]:
    """``(flow, notice)``: start the OAuth flow (REQ-AUTH-002); ``flow`` is ``None`` when
    it could not start. The caller opens ``flow.authorization_url`` with an external
    redirect and never stores or logs it (REQ-AUTH-008)."""
    try:
        flow = await host.connections.authorize(server_id)
    except (MCPHostError, ValueError) as exc:
        return None, _failed(f"Authorize {server_id}", exc)
    return flow, f"Authorize {server_id}: continue in the browser tab"


async def authorization_outcome(flow: Any) -> str:
    """The flow's outcome: success, or its ``AuthorizationError.reason`` code."""
    try:
        await flow.wait()
    except AuthorizationError as exc:
        return f"Authorize {flow.server_id} failed: {exc.reason}"
    return f"Authorize {flow.server_id}: signed in"


# ------------------------------------------------------------------ Catalog (F6-05)


async def invoke(
    host: Any,
    fqn: str,
    form_data: Mapping[str, Any],
    commit: Commit,
    *,
    token: str,
    handles: dict[str, Any] = INVOCATIONS,
) -> None:
    """Coerce the form, start the invocation as ``human:ui`` and commit its progress and
    result (``invocation``) and any field errors (``form_errors``)."""
    try:
        entry = host.catalog.get_tool(fqn)
    except (MCPHostError, ValueError) as exc:
        await commit({"form_errors": {"": str(exc)}})
        return
    args, errors = models.coerce_form(entry.input_schema, form_data)
    if errors:
        await commit({"form_errors": errors})
        return
    try:
        handle = host.invoker.start(fqn, args, consumer=HUMAN)
    except (MCPHostError, RuntimeError, ValueError) as exc:
        await commit({"form_errors": {"": str(exc)}})
        return
    handles[token] = handle
    view: dict[str, Any] = {"id": handle.id, "fqn": fqn, "running": True, "progress": []}
    await commit({"form_errors": {}, "invocation": {**view, "result": {}}})
    try:
        async for progress in handle.progress():
            view["progress"] = [*view["progress"], models.progress_row(progress)]
            await commit({"invocation": {**view, "result": {}}})
        result = await handle.result()
    finally:
        if handles.get(token) is handle:
            del handles[token]
    issues = result.error.details if result.error is not None else None
    await commit(
        {
            "form_errors": models.issue_errors(issues),
            "invocation": {**view, "running": False, "result": models.result_view(result)},
        }
    )


async def cancel_invocation(token: str, handles: dict[str, Any] = INVOCATIONS) -> bool:
    """Cancel this tab's running invocation (REQ-INV-004); ``False`` if none runs."""
    handle = handles.get(token)
    if handle is None:
        return False
    await handle.cancel()
    return True


def _read_view(kind: str, title: str, blocks: list[models.Row], error: str = "") -> models.Row:
    return {"kind": kind, "title": title, "blocks": blocks, "error": error}


def _strings(form_data: Mapping[str, Any]) -> dict[str, str]:
    return {str(k): v for k, v in form_data.items() if isinstance(v, str) and v != ""}


async def read_resource(host: Any, server_id: str, uri: str) -> models.Row:
    """``resources/read`` as ``human:ui`` (REQ-CAT-005)."""
    try:
        contents = await host.catalog.read_resource(server_id, uri, consumer=HUMAN)
    except (MCPHostError, ValueError) as exc:
        return _read_view("resource", uri, [], str(exc))
    return _read_view("resource", uri, models.resource_rows(contents.contents))


async def read_template(host: Any, fqn: str, form_data: Mapping[str, Any]) -> models.Row:
    """Expand the template with the form's variables and read it (REQ-CAT-006)."""
    try:
        contents = await host.catalog.read_template(fqn, _strings(form_data), consumer=HUMAN)
    except (MCPHostError, ValueError) as exc:
        return _read_view("template", fqn, [], str(exc))
    return _read_view("template", contents.uri, models.resource_rows(contents.contents))


async def get_prompt(host: Any, fqn: str, form_data: Mapping[str, Any]) -> models.Row:
    """``prompts/get`` with the form's arguments (REQ-CAT-007)."""
    try:
        result = await host.catalog.get_prompt(fqn, _strings(form_data), consumer=HUMAN)
    except (MCPHostError, ValueError) as exc:
        return _read_view("prompt", fqn, [], str(exc))
    return _read_view("prompt", fqn, models.prompt_rows(result.messages))


# ------------------------------------------------------------------ Permissions, Audit (F6-07)


def rule_view(host: Any) -> list[models.Row]:
    """Persistent and session rules (REQ-CNS-013)."""
    return models.rule_rows(host.consent.rules())


async def update_rule(host: Any, rule_id: str, effect: str, lifetime: str) -> str:
    """Edit a rule's effect / lifetime; the next evaluation sees it (REQ-CNS-013)."""
    if effect not in ("allow", "deny") or lifetime not in ("session", "always"):
        return "Edit failed: choose allow / deny and session / always"
    try:
        await host.consent.update(rule_id, effect=effect, lifetime=lifetime)
    except (MCPHostError, LookupError, ValueError) as exc:
        return _failed("Edit", exc)
    return f"Rule {rule_id}: {effect}, {lifetime}"


async def revoke_rule(host: Any, rule_id: str) -> str:
    """Delete a rule; the next evaluation no longer sees it (REQ-CNS-013)."""
    try:
        await host.consent.revoke(rule_id)
    except (MCPHostError, LookupError, ValueError) as exc:
        return _failed("Revoke", exc)
    return f"Rule {rule_id}: revoked"


async def audit_page(
    host: Any, filters: Mapping[str, str], cursor: str | None = None
) -> tuple[list[models.Row], str, str]:
    """``(rows, next_cursor, notice)`` of one Audit page, newest first (REQ-INV-011)."""
    query, problem = models.audit_query(filters, cursor)
    if query is None:
        return [], "", problem
    try:
        result = await host.audit.query(query)
    except MCPHostError as exc:
        return [], "", _failed("Audit query", exc)
    return models.audit_rows(result.records), result.next_cursor or "", ""


async def refresh_audit(host: Any, filters: Mapping[str, str] | None, commit: Commit) -> None:
    """Re-query the first Audit page on ``invocation.finished``; ``filters`` is ``None``
    for a tab that never opened the Audit view (nothing is queried)."""
    if filters is None:
        return
    rows, cursor, notice = await audit_page(host, filters)
    await commit({"audit": rows, "audit_cursor": cursor, "audit_notice": notice})
