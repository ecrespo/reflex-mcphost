"""``core.apps.messages``: JSON-RPC 2.0 shape validation and limits (F7-02; API §7)."""

from __future__ import annotations

import json

import pytest

from reflex_mcphost.core.apps.constants import MAX_MESSAGE_BYTES
from reflex_mcphost.core.apps.messages import (
    INVALID_REQUEST,
    PARSE_ERROR,
    Invalid,
    Notification,
    Request,
    Response,
    error_message,
    notification_message,
    parse_message,
    request_message,
    result_message,
)


def test_request_notification_response() -> None:
    """REQ-APP-003 (API §7): requests, notifications and responses are recognized."""
    assert parse_message('{"jsonrpc":"2.0","id":1,"method":"ping"}') == Request(1, "ping", {})
    assert parse_message('{"jsonrpc":"2.0","id":"a","method":"m","params":{"x":1}}') == Request(
        "a", "m", {"x": 1}
    )
    assert parse_message('{"jsonrpc":"2.0","method":"n"}') == Notification("n", {})
    assert parse_message('{"jsonrpc":"2.0","id":"h1","result":{}}') == Response("h1", {}, None)
    err = {"code": -1, "message": "x"}
    assert parse_message(json.dumps({"jsonrpc": "2.0", "id": 2, "error": err})) == Response(
        2, None, err
    )
    assert parse_message(b'{"jsonrpc":"2.0","method":"n"}') == Notification("n", {})


@pytest.mark.parametrize(
    ("raw", "code", "msg_id"),
    [
        ("not json", PARSE_ERROR, None),
        (b"\xff\xfe", PARSE_ERROR, None),
        ('[{"jsonrpc":"2.0","id":1,"method":"ping"}]', INVALID_REQUEST, None),
        ("[]", INVALID_REQUEST, None),
        ('"text"', INVALID_REQUEST, None),
        ('{"id":1,"method":"ping"}', INVALID_REQUEST, 1),
        ('{"jsonrpc":"1.0","id":1,"method":"ping"}', INVALID_REQUEST, 1),
        ('{"jsonrpc":"2.0","id":1.5,"method":"ping"}', INVALID_REQUEST, None),
        ('{"jsonrpc":"2.0","id":true,"method":"ping"}', INVALID_REQUEST, None),
        ('{"jsonrpc":"2.0","id":null,"method":"ping"}', INVALID_REQUEST, None),
        ('{"jsonrpc":"2.0","id":{},"method":"ping"}', INVALID_REQUEST, None),
        (json.dumps({"jsonrpc": "2.0", "id": 3, "method": "m" * 129}), INVALID_REQUEST, 3),
        ('{"jsonrpc":"2.0","id":4,"method":7}', INVALID_REQUEST, 4),
        ('{"jsonrpc":"2.0","id":5,"method":"m","params":[1]}', INVALID_REQUEST, 5),
        ('{"jsonrpc":"2.0","method":"m","params":"x"}', INVALID_REQUEST, None),
        ('{"jsonrpc":"2.0","id":6}', INVALID_REQUEST, 6),
        ('{"jsonrpc":"2.0","id":7,"result":{},"error":{}}', INVALID_REQUEST, 7),
        ('{"jsonrpc":"2.0","result":{}}', INVALID_REQUEST, None),
    ],
)
def test_malformed(raw: str | bytes, code: int, msg_id: object) -> None:
    """REQ-APP-005 (API §7): not JSON → ``-32700``; batches, non-objects, a missing or wrong
    ``jsonrpc``, a bad ``id`` type, a method that is not a string ≤ 128 characters,
    non-object ``params`` and malformed responses → ``-32600``, echoing the id when it is
    readable."""
    parsed = parse_message(raw)
    assert isinstance(parsed, Invalid)
    assert (parsed.code, parsed.id, parsed.reply) == (code, msg_id, True)


def test_method_length_boundary() -> None:
    """REQ-APP-005: a 128-character method is accepted."""
    assert isinstance(
        parse_message(json.dumps({"jsonrpc": "2.0", "method": "m" * 128})), Notification
    )


def test_oversized_messages() -> None:
    """REQ-APP-005, REQ-APP-013 (API §7): > 1 MiB serialized → ``-32600`` when an id can be
    read, dropped (no reply) otherwise; exactly 1 MiB is accepted."""
    filler = "x" * MAX_MESSAGE_BYTES
    with_id = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "m", "params": {"f": filler}})
    parsed = parse_message(with_id)
    assert isinstance(parsed, Invalid)
    assert (parsed.code, parsed.id, parsed.reply) == (INVALID_REQUEST, 9, True)
    without = json.dumps({"jsonrpc": "2.0", "method": "m", "params": {"f": filler}})
    dropped = parse_message(without)
    assert isinstance(dropped, Invalid)
    assert dropped.reply is False
    junk = parse_message("{" + filler)
    assert isinstance(junk, Invalid)
    assert junk.reply is False
    huge = parse_message(
        json.dumps({"jsonrpc": "2.0", "id": 1, "f": "x" * (5 * MAX_MESSAGE_BYTES)})
    )
    assert isinstance(huge, Invalid)
    assert huge.reply is False
    base = json.dumps({"jsonrpc": "2.0", "method": "m", "params": {"f": ""}})
    exact = base.replace('"f": ""', '"f": "' + "y" * (MAX_MESSAGE_BYTES - len(base)) + '"')
    assert len(exact.encode()) == MAX_MESSAGE_BYTES
    assert isinstance(parse_message(exact), Notification)


def test_builders() -> None:
    """REQ-APP-003: messages the Host sends are compact JSON-RPC 2.0."""
    assert json.loads(result_message(1, {})) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert json.loads(error_message("a", -32601, "nope")) == {
        "jsonrpc": "2.0",
        "id": "a",
        "error": {"code": -32601, "message": "nope"},
    }
    assert json.loads(error_message(None, -32700, "Parse error"))["id"] is None
    assert json.loads(notification_message("n")) == {"jsonrpc": "2.0", "method": "n"}
    assert json.loads(notification_message("n", {"a": 1}))["params"] == {"a": 1}
    assert json.loads(request_message("h1", "r", {"b": 2})) == {
        "jsonrpc": "2.0",
        "id": "h1",
        "method": "r",
        "params": {"b": 2},
    }
