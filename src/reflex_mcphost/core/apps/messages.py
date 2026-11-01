"""JSON-RPC 2.0 messages between a View and the Host (API §7 "Transport", delta D5).

``parse_message(raw)`` validates one View → Host message: a JSON object (batches are
refused) with ``jsonrpc == "2.0"``, ``id`` a string or integer when present, ``method`` a
string of at most 128 characters, ``params`` an object when present, serialized at most
1 MiB. A failure is an ``Invalid`` carrying the reply code and the id when it could be
read; ``reply=False`` means the message is dropped silently (an oversized message without a
readable id). Messages the Host sends are built with the ``*_message`` helpers (compact
JSON).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from reflex_mcphost.core.apps.constants import MAX_MESSAGE_BYTES, MAX_METHOD_CHARS

__all__ = [
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "SERVER_ERROR",
    "Invalid",
    "JsonRpcId",
    "Message",
    "Notification",
    "Request",
    "Response",
    "error_message",
    "notification_message",
    "parse_message",
    "request_message",
    "result_message",
]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
SERVER_ERROR = -32000
JsonRpcId = str | int
_OVERSIZE_PARSE_LIMIT = 4 * MAX_MESSAGE_BYTES
"""Oversized messages up to this size are parsed only to read their id; larger ones are
dropped unread."""


@dataclass(frozen=True)
class Request:
    id: JsonRpcId
    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Notification:
    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Response:
    """A View's reply to a Host request (``ui/resource-teardown``)."""

    id: JsonRpcId
    result: dict[str, Any] | None
    error: dict[str, Any] | None


@dataclass(frozen=True)
class Invalid:
    code: int
    message: str
    id: JsonRpcId | None = None
    reply: bool = True


Message = Request | Notification | Response | Invalid


def _valid_id(value: object) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _oversized(raw: str | bytes, size: int) -> Invalid:
    if size <= _OVERSIZE_PARSE_LIMIT:
        try:
            obj = json.loads(raw)
        except (ValueError, RecursionError):
            obj = None
        if isinstance(obj, dict) and "method" in obj and _valid_id(obj.get("id")):
            return Invalid(INVALID_REQUEST, "message too large", obj["id"])
    return Invalid(INVALID_REQUEST, "message too large", reply=False)


def parse_message(raw: str | bytes) -> Message:
    """Validate one serialized View message (never raises)."""
    size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8", "surrogatepass"))
    if size > MAX_MESSAGE_BYTES:
        return _oversized(raw, size)
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return Invalid(PARSE_ERROR, "Parse error")
    if isinstance(obj, list):
        return Invalid(INVALID_REQUEST, "batches are not supported")
    if not isinstance(obj, dict):
        return Invalid(INVALID_REQUEST, "a message must be a JSON object")
    has_id = "id" in obj
    msg_id = obj.get("id")
    if has_id and not _valid_id(msg_id):
        return Invalid(INVALID_REQUEST, "id must be a string or an integer")
    echo: JsonRpcId | None = msg_id if has_id else None
    if obj.get("jsonrpc") != "2.0":
        return Invalid(INVALID_REQUEST, 'jsonrpc must be "2.0"', echo)
    if "method" in obj:
        method = obj["method"]
        if not isinstance(method, str) or not method or len(method) > MAX_METHOD_CHARS:
            return Invalid(
                INVALID_REQUEST, "method must be a string of at most 128 characters", echo
            )
        params = obj.get("params", {})
        if not isinstance(params, dict):
            return Invalid(INVALID_REQUEST, "params must be an object", echo)
        if echo is not None:
            return Request(echo, method, params)
        return Notification(method, params)
    if echo is None or ("result" in obj) == ("error" in obj):
        return Invalid(INVALID_REQUEST, "not a request, notification or response", echo)
    result, error = obj.get("result"), obj.get("error")
    if (result is not None and not isinstance(result, dict)) or (
        error is not None and not isinstance(error, dict)
    ):
        return Invalid(INVALID_REQUEST, "malformed response", echo)
    return Response(echo, result, error)


def _dump(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False)


def result_message(msg_id: JsonRpcId, result: dict[str, Any]) -> str:
    return _dump({"jsonrpc": "2.0", "id": msg_id, "result": result})


def error_message(msg_id: JsonRpcId | None, code: int, message: str) -> str:
    return _dump({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def notification_message(method: str, params: dict[str, Any] | None = None) -> str:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return _dump(message)


def request_message(msg_id: JsonRpcId, method: str, params: dict[str, Any] | None = None) -> str:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return _dump(message)
