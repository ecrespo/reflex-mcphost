"""The Client ID Metadata Document published on GitHub Pages (F4-10, DD-009, API §2.2)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp.client.auth.utils import is_valid_client_metadata_url
from mcp.shared.auth import OAuthClientMetadata

from reflex_mcphost.core.config.models import DEFAULT_OAUTH_CLIENT_METADATA_URL

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs" / "oauth" / "client.json"
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
_SECRET_KEY = re.compile(r"(?i)secret|password|private|jwks|^token$|access|refresh")


def _load() -> dict[str, Any]:
    data = json.loads(DOCUMENT.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_document_is_valid_client_metadata() -> None:
    """REQ-AUTH-012: the versioned CIMD parses as SDK client metadata and its client_id is
    the default oauth_client_metadata_url, a valid CIMD URL (HTTPS, non-root path)."""
    data = _load()
    metadata = OAuthClientMetadata.model_validate(data)
    assert data["client_id"] == DEFAULT_OAUTH_CLIENT_METADATA_URL
    assert is_valid_client_metadata_url(data["client_id"])
    assert metadata.grant_types == ["authorization_code", "refresh_token"]
    assert metadata.response_types == ["code"]
    assert metadata.token_endpoint_auth_method == "none"  # noqa: S105 - method name
    assert metadata.application_type == "native"
    assert data["client_name"]
    assert data["client_uri"].startswith("https://")


CIMD_URL = "https://blog.seraph.to/reflex-mcphost/oauth/client.json"
"""The frozen CIMD URL (owner decision 2026-11-01, delta ``2026-11-cimd-url``)."""


def test_client_id_is_the_non_redirecting_custom_domain_url() -> None:
    """REQ-AUTH-012: the default ``oauth_client_metadata_url`` and the document's
    ``client_id`` are exactly the URL the authorization server fetches — the owner's Pages
    custom domain, not ``ecrespo.github.io`` (which answers ``301`` to it; a CIMD
    ``client_id`` must not depend on a redirect)."""
    assert DEFAULT_OAUTH_CLIENT_METADATA_URL == CIMD_URL
    assert _load()["client_id"] == DEFAULT_OAUTH_CLIENT_METADATA_URL
    assert urlsplit(CIMD_URL).hostname != "ecrespo.github.io"


def test_document_lists_only_portless_loopback_redirects() -> None:
    """REQ-AUTH-012: only loopback redirect URIs, portless (RFC 8252 §7.3 port variance)."""
    uris = _load()["redirect_uris"]
    assert uris == ["http://127.0.0.1/callback"]
    for uri in uris:
        parts = urlsplit(uri)
        assert (parts.scheme, parts.hostname, parts.port) == ("http", "127.0.0.1", None)


def test_document_carries_no_secret_like_key() -> None:
    """REQ-AUTH-012, Art. 5: the public document names no secret-like member."""
    assert [k for k in _load() if _SECRET_KEY.search(k)] == []


def test_pages_workflow_publishes_docs_with_least_privilege() -> None:
    """REQ-AUTH-012: the Pages job builds ``docs/`` into ``_site`` (so ``oauth/client.json`` is
    served at the client_id URL) on push to ``main`` with only the permissions Pages needs."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "branches: [main]" in text
    assert "actions/upload-pages-artifact@" in text
    assert "source: ./docs" in text
    assert "path: _site" in text
    assert "actions/deploy-pages@" in text
    for permission in ("contents: read", "pages: write", "id-token: write"):
        assert permission in text
    assert urlsplit(DEFAULT_OAUTH_CLIENT_METADATA_URL).path.endswith("/oauth/client.json")
