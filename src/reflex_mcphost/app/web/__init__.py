"""Packaged Reflex sources of the reference app (DD-016).

``rxconfig.py`` and ``mcphost_app/`` are copied into a per-user workspace by
``reflex_mcphost.app.workspace`` and run there as the top-level modules ``rxconfig`` and
``mcphost_app.mcphost_app``; this ``__init__`` only makes the directory a regular package
for type checking and import contracts and is not copied.
"""
