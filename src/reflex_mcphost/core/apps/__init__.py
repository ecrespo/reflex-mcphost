"""Pure-Python MCP Apps support (DD-012, API §7): ``constants`` (extension id, MIME type,
limits, ``app_visible``), ``csp`` (``build_csp``), ``resource`` (``select_view_html``),
``messages`` (JSON-RPC shape validation) and ``bridge`` (``AppBridge``, the per-View state
machine). No Reflex, no Starlette, no socket or file I/O; nothing from ``mcp.server``.

The submodules are imported explicitly (``reflex_mcphost.core.apps.bridge``): this package
re-exports nothing, because ``core.invoker`` imports ``core.apps.constants`` while
``core.apps.bridge`` imports ``core.invoker``.
"""
