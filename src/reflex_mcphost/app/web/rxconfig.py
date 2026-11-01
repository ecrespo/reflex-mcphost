"""Reflex config of the reference app, read in the materialized workspace (DD-016, DD-017).

The CLI passes host and port only through ``REFLEX_MCPHOST_HOST`` / ``REFLEX_MCPHOST_PORT``
(default ``127.0.0.1:3000``). ``api_url`` and ``deploy_url`` are the single origin
``http://<host>:<port>`` (the default ``api_url`` would use ``localhost``, which the guard
refuses), ``cors_allowed_origins`` is that origin alone, the backend binds the same host
and serves UI and backend on one port (prod), telemetry is off and only the Radix Themes
plugin is enabled (explicitly; no sitemap).
"""

from __future__ import annotations

import os

import reflex as rx

_HOST = os.environ.get("REFLEX_MCPHOST_HOST") or "127.0.0.1"
_PORT = int(os.environ.get("REFLEX_MCPHOST_PORT") or "3000")
_ORIGIN = f"http://{f'[{_HOST}]' if ':' in _HOST else _HOST}:{_PORT}"

config = rx.Config(
    app_name="mcphost_app",
    telemetry_enabled=False,
    api_url=_ORIGIN,
    deploy_url=_ORIGIN,
    cors_allowed_origins=[_ORIGIN],
    backend_host=_HOST,
    frontend_port=_PORT,
    backend_port=_PORT,
    plugins=[rx.plugins.RadixThemesPlugin()],  # explicit (implicit enablement is deprecated)
    disable_plugins=[rx.plugins.SitemapPlugin],
)
