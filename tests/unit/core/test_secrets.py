"""Secret registry (Constitution Art. 5)."""

from __future__ import annotations

import threading

from pydantic import SecretStr

from reflex_mcphost.core.secrets import MASK, SecretRegistry, reveal


def test_registered_value_is_masked() -> None:
    """REQ-NFR-010: a registered value never survives redaction."""
    reg = SecretRegistry()
    reg.register("tok-123456")
    assert reg.redact("Bearer tok-123456 sent") == f"Bearer {MASK} sent"


def test_longest_value_wins() -> None:
    """REQ-NFR-010: overlapping secrets leave no partial suffix behind."""
    reg = SecretRegistry()
    reg.register("abcd")
    reg.register("abcdefgh")
    assert reg.redact("x abcdefgh y") == f"x {MASK} y"


def test_empty_values_are_ignored_and_forget_works() -> None:
    """Art. 5: empty strings are never registered; forgotten values stop being masked."""
    reg = SecretRegistry()
    reg.register("")
    assert reg.redact("text") == "text"
    reg.register("s3cr3t-value")
    reg.forget("s3cr3t-value")
    assert reg.redact("s3cr3t-value") == "s3cr3t-value"


def test_reveal_registers_the_value() -> None:
    """REQ-NFR-010: revealing a SecretStr registers it for redaction."""
    reg = SecretRegistry()
    raw = reveal(SecretStr("pw-xyz-987"), registry=reg)
    assert raw == "pw-xyz-987"
    assert reg.redact("pw-xyz-987") == MASK


def test_concurrent_registration() -> None:
    """Art. 5: registration is thread-safe."""
    reg = SecretRegistry()
    values = [f"secret-{i:04d}" for i in range(200)]
    threads = [threading.Thread(target=reg.register, args=(v,)) for v in values]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(reg.redact(v) == MASK for v in values)
