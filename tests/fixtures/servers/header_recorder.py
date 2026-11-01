"""ASGI middleware recording the request headers of the HTTP fixture Server (test-only).

Each HTTP request appends one JSON line ``{"method", "path", "headers": {name: value}}`` to
the file given by ``--record-headers``; header names are lower-cased (ASGI). Tests read it
back with ``read_records``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

Scope = dict[str, Any]


class HeaderRecorder:
    def __init__(self, app: Any, path: Path) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            record = {
                "method": scope["method"],
                "path": scope["path"],
                "headers": {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]},
            }
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        await self.app(scope, receive, send)


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
