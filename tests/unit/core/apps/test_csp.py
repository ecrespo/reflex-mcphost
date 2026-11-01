"""``core.apps.build_csp``: entry grammar and the pinned policy (F7-01; API §7, delta D3)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from reflex_mcphost.core.apps.csp import (
    CSP_LISTS,
    DEFAULT_POLICY,
    MAX_ENTRIES,
    MAX_ENTRY_CHARS,
    CspResult,
    build_csp,
    valid_entry,
)

PINNED_DEFAULT = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; "
    "connect-src 'none'; frame-src 'none'; base-uri 'self'; object-src 'none'; "
    "form-action 'none'"
)
DIRECTIVES = (
    "default-src",
    "script-src",
    "style-src",
    "img-src",
    "font-src",
    "media-src",
    "connect-src",
    "frame-src",
    "base-uri",
    "object-src",
    "form-action",
)
KEYWORDS = {"'none'", "'unsafe-inline'", "'self'", "data:", "blob:"}


def _directives(policy: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in policy.split("; "):
        name, *sources = part.split(" ")
        out[name] = sources
    return out


@pytest.mark.parametrize("meta", [None, {}, {"connectDomains": []}, "nonsense", 7])
def test_default_policy_is_pinned(meta: object) -> None:
    """REQ-APP-002, REQ-APP-014 (API §7): no declared domains → exactly the pinned policy
    string, never ``'unsafe-eval'``; nothing external, nothing dropped."""
    result = build_csp(meta)
    assert result.policy == PINNED_DEFAULT == DEFAULT_POLICY
    assert "'unsafe-eval'" not in result.policy
    assert result.external is False
    assert result.dropped == ()


def test_policy_with_every_list() -> None:
    """REQ-APP-002, REQ-APP-011 (API §7): R goes to script/style/img/font/media, C to
    connect, F to frame, B to base-uri — in the pinned order; valid external domains set
    the ``external`` flag that drives the notice."""
    result = build_csp(
        {
            "resourceDomains": ["https://cdn.example.com"],
            "connectDomains": ["https://api.example.com", "wss://rt.example.com"],
            "frameDomains": ["https://*.frames.example.org:8443"],
            "baseUriDomains": ["https://base.example.net"],
        }
    )
    assert result.policy == (
        "default-src 'none'; script-src 'unsafe-inline' https://cdn.example.com; "
        "style-src 'unsafe-inline' https://cdn.example.com; "
        "img-src data: blob: https://cdn.example.com; font-src data: https://cdn.example.com; "
        "media-src data: blob: https://cdn.example.com; "
        "connect-src https://api.example.com wss://rt.example.com; "
        "frame-src https://*.frames.example.org:8443; base-uri https://base.example.net; "
        "object-src 'none'; form-action 'none'"
    )
    assert result.external is True
    assert result.dropped == ()
    assert result.domains["connectDomains"] == ("https://api.example.com", "wss://rt.example.com")


@pytest.mark.parametrize(
    "entry",
    [
        "*",
        "https:",
        "'unsafe-eval'",
        "'self'",
        "data:",
        "http://cdn.example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://localhost",
        "https://a.localhost",
        "https://LOCALHOST",
        "https://x.example/p",
        "https://x.example/",
        "https://x.example?q=1",
        "https://x.example#f",
        "https://u@x.example",
        "https://u:p@x.example",
        "https://x.example;script-src *",
        "https://x.example,https://y.example",
        "https://x.example com",
        " https://x.example",
        "https://x.example\t",
        'https://"x".example',
        "https://*",
        "https://*.com",
        "https://a.*.example.com",
        "https://*.*.example.com",
        "https://intranet",
        "https://x.example:0",
        "https://x.example:65536",
        "https://x.example:",
        "https://x.example:80a",
        "https://-x.example.com",
        "https://x-.example.com",
        "https://x..example.com",
        "https://x.example.com.",
        "https://printer.local",
        "https://db.internal",
        "https://1.2.3.4.in-addr.arpa",
        "https://10.0.0.1",
        "https://" + "a" * 64 + ".example.com",
        "https://" + ("a" * 60 + ".") * 4 + "com",
        "",
        "ftp://x.example.com",
        "HTTPS://x.example.com",
    ],
)
def test_invalid_entries_are_dropped(entry: str) -> None:
    """REQ-APP-014 (Art. 5): wildcards, scheme-only sources, keywords, plain http, IP
    literals, ``localhost``/``*.localhost``, paths, queries, userinfo, ``;``, ``,``,
    whitespace and quotes never reach the policy; each lands in ``dropped``."""
    for name in CSP_LISTS:
        result = build_csp({name: [entry]})
        assert result.policy == PINNED_DEFAULT, (name, entry)
        shown = entry if len(entry) <= MAX_ENTRY_CHARS else entry[:MAX_ENTRY_CHARS] + "…"
        assert [(d.list_name, d.entry) for d in result.dropped] == [(name, shown)]
        assert result.external is False


def test_wss_only_in_connect_domains() -> None:
    """REQ-APP-014: ``wss://`` is valid only in ``connectDomains``."""
    for name in ("resourceDomains", "frameDomains", "baseUriDomains"):
        result = build_csp({name: ["wss://rt.example.com"]})
        assert result.policy == PINNED_DEFAULT
        assert [d.entry for d in result.dropped] == ["wss://rt.example.com"]
    connect = build_csp({"connectDomains": ["wss://rt.example.com"]})
    assert "connect-src wss://rt.example.com;" in connect.policy
    assert connect.external is True


@pytest.mark.parametrize(
    "entry",
    [
        "https://cdn.example.com",
        "https://*.example.com:8443",
        "https://a-b.c-d.example.co.uk",
        "https://xn--bcher-kva.example",
        "https://CDN.Example.COM",
        "https://x.example:1",
        "https://x.example:65535",
    ],
)
def test_valid_entries(entry: str) -> None:
    """REQ-APP-014: public DNS host names with an optional leading ``*.`` and port."""
    assert valid_entry(entry, "resourceDomains")
    result = build_csp({"resourceDomains": [entry]})
    assert f"script-src 'unsafe-inline' {entry};" in result.policy
    assert result.external is True
    assert result.dropped == ()


def test_non_string_entries_and_lists() -> None:
    """REQ-APP-014: a non-list value or non-string entries are dropped (non-strings are
    named by type)."""
    result = build_csp(
        {"resourceDomains": "https://cdn.example.com", "connectDomains": [1, None, {"a": 1}]}
    )
    assert result.policy == PINNED_DEFAULT
    assert [(d.list_name, d.entry) for d in result.dropped] == [
        ("connectDomains", "<int>"),
        ("connectDomains", "<NoneType>"),
        ("connectDomains", "<dict>"),
        ("resourceDomains", "https://cdn.example.com"),
    ]


def test_limits_per_list_and_entry() -> None:
    """REQ-APP-014 (delta D3): at most 32 entries per list and 253 characters per entry
    are considered; the rest is dropped the same way; dropped text is bounded."""
    many = [f"https://h{i}.example.com" for i in range(MAX_ENTRIES + 3)]
    result = build_csp({"connectDomains": many})
    connect = _directives(result.policy)["connect-src"]
    assert connect == many[:MAX_ENTRIES]
    assert [d.entry for d in result.dropped] == many[MAX_ENTRIES:]
    long_entry = "https://" + "a." * 130 + "com"
    assert len(long_entry) > MAX_ENTRY_CHARS
    dropped = build_csp({"resourceDomains": [long_entry]}).dropped
    assert len(dropped) == 1
    assert len(dropped[0].entry) <= MAX_ENTRY_CHARS + 1
    assert dropped[0].entry.endswith("…")


def test_duplicates_kept_once_and_unknown_keys_ignored() -> None:
    """REQ-APP-002: a repeated valid entry appears once; unknown keys are ignored."""
    result = build_csp(
        {"resourceDomains": ["https://a.example.com", "https://a.example.com"], "other": ["*"]}
    )
    assert _directives(result.policy)["img-src"] == ["data:", "blob:", "https://a.example.com"]
    assert result.dropped == ()


def test_result_is_frozen() -> None:
    """REQ-APP-014: ``CspResult`` is an immutable value."""
    result = build_csp(None)
    assert isinstance(result, CspResult)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        result.policy = "x"  # type: ignore[misc]


_text = st.one_of(
    st.text(max_size=40),
    st.sampled_from(
        [
            "*",
            "https:",
            "'unsafe-eval'",
            "https://ok.example.com",
            "wss://rt.example.com",
            "https://*.example.com:443",
            "https://x.example; script-src *",
            "https://127.0.0.1",
        ]
    ),
    st.builds(lambda h: f"https://{h}.example.com", st.text(max_size=12)),
)


@settings(max_examples=300, deadline=None)
@given(
    st.dictionaries(
        st.sampled_from([*CSP_LISTS, "junk"]),
        st.one_of(st.lists(st.one_of(_text, st.integers(), st.none()), max_size=40), _text),
        max_size=5,
    )
)
def test_property_policy_shape(meta: dict[str, object]) -> None:
    """REQ-APP-002, REQ-APP-014 (Hypothesis): for arbitrary lists the policy has exactly
    the 11 pinned directives in order, never ``'unsafe-eval'``, and every source token is
    a pinned keyword or a valid entry of the list feeding that directive."""
    result = build_csp(meta)
    directives = _directives(result.policy)
    assert tuple(directives) == DIRECTIVES
    assert "'unsafe-eval'" not in result.policy
    feeds = {
        "script-src": "resourceDomains",
        "style-src": "resourceDomains",
        "img-src": "resourceDomains",
        "font-src": "resourceDomains",
        "media-src": "resourceDomains",
        "connect-src": "connectDomains",
        "frame-src": "frameDomains",
        "base-uri": "baseUriDomains",
    }
    for name, sources in directives.items():
        assert sources, name
        for token in sources:
            assert token in KEYWORDS or (name in feeds and valid_entry(token, feeds[name])), (
                name,
                token,
            )
    assert result.external == any(result.domains[n] for n in CSP_LISTS)
