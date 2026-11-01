"""``CapabilityPlan.mcp_apps`` → the Client's ``extensions`` (F7-01; REQ-APP-007, DD-012)."""

from __future__ import annotations

from mcp.client import ClientExtension

from reflex_mcphost.core.apps.constants import EXTENSION_ID, MIME_TYPE
from reflex_mcphost.core.handlers.capabilities import CapabilityPlan, HandlerSet


def _attribute(method: str, key: str) -> None:  # pragma: no cover - never called
    return None


def test_extension_only_when_planned() -> None:
    """REQ-APP-007: ``extensions`` is passed iff the plan enables MCP Apps, as one
    ``advertise(EXTENSION_ID, {"mimeTypes": [MIME_TYPE]})`` entry; it declares no input
    request method."""
    handlers = HandlerSet()
    assert "extensions" not in handlers.client_kwargs(CapabilityPlan(), _attribute)  # type: ignore[arg-type]
    plan = CapabilityPlan(mcp_apps=True)
    kwargs = handlers.client_kwargs(plan, _attribute)  # type: ignore[arg-type]
    (extension,) = kwargs["extensions"]
    assert isinstance(extension, ClientExtension)
    assert extension.identifier == EXTENSION_ID
    assert extension.settings() == {"mimeTypes": [MIME_TYPE]}
    assert plan.methods == frozenset()
    assert CapabilityPlan(mcp_apps=True) != CapabilityPlan()  # a change triggers a rebuild
