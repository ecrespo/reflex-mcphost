"""ASGI bearer-token guard for the HTTP fixture Servers (Tasks F4-09a; test-only code).

Wraps the modern and the legacy fixture app (``--bearer-check AS_BASE``), so it uses only
the standard library and ``anyio`` (both Server environments have them):

- ``/.well-known/oauth-protected-resource[/mcp]`` → RFC 9728 metadata naming the fixture
  AS's *current* issuer (``AS_BASE/_fixture/issuer``) and ``resource = <base>/mcp``;
- any other request without a bearer token the AS introspects as active →
  ``401`` with ``WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/
  oauth-protected-resource/mcp"``.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

import anyio.to_thread

PRM_PATH = "/.well-known/oauth-protected-resource"


def _post_form(url: str, data: dict[str, str]) -> Any:
    body = urllib.parse.urlencode(data).encode("ascii")
    with urllib.request.urlopen(url, data=body, timeout=5) as response:  # noqa: S310 - fixture AS
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - fixture AS
        return json.loads(response.read().decode("utf-8"))


async def _send(send: Any, status: int, body: bytes, headers: list[tuple[bytes, bytes]]) -> None:
    headers = [*headers, (b"content-length", b"%d" % len(body))]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class BearerCheck:
    def __init__(self, app: Any, as_base: str) -> None:
        self.app = app
        self.as_base = as_base.rstrip("/")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        base = f"http://{headers.get('host', '127.0.0.1')}"
        if scope["path"].startswith(PRM_PATH):
            issuer = (await anyio.to_thread.run_sync(_get_json, f"{self.as_base}/_fixture/issuer"))[
                "issuer"
            ]
            doc = {
                "resource": f"{base}/mcp",
                "authorization_servers": [issuer],
                "bearer_methods_supported": ["header"],
            }
            await _send(
                send, 200, json.dumps(doc).encode(), [(b"content-type", b"application/json")]
            )
            return
        auth = headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        if token:
            result = await anyio.to_thread.run_sync(
                _post_form,
                f"{self.as_base}/introspect",
                {"token": token, "host": headers.get("host", "")},
            )
            if result.get("active") is True:
                await self.app(scope, receive, send)
                return
        challenge = f'Bearer resource_metadata="{base}{PRM_PATH}/mcp"'
        await _send(
            send,
            401,
            b'{"error": "invalid_token"}',
            [(b"content-type", b"application/json"), (b"www-authenticate", challenge.encode())],
        )
