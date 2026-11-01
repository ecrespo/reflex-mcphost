"""Tasks F4-10, F8-07: the GitHub Pages site built from ``docs/``.

The site root must answer (a landing page, not a 404), the Markdown guides must be rendered
to HTML, internal notes stay out of it, and the Client ID Metadata Document must still be
published byte-for-byte (REQ-AUTH-012).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


@pytest.fixture(scope="module")
def deploy_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/pages.yml").read_text())
    return workflow["jobs"]["deploy"]["steps"]


def test_pages_renders_docs_with_jekyll(deploy_steps: list[dict[str, Any]]) -> None:
    """The Markdown guides are rendered to HTML (not served raw), from ``docs/``."""
    build = [s for s in deploy_steps if "actions/jekyll-build-pages" in str(s.get("uses", ""))]
    assert len(build) == 1
    (step,) = build
    assert "@" in step["uses"]
    assert len(step["uses"].split("@", 1)[1].split()[0]) == 40  # pinned to a commit SHA
    assert step["with"]["source"] == "./docs"
    assert step["with"]["destination"] == "./_site"
    upload = [s for s in deploy_steps if "upload-pages-artifact" in str(s.get("uses", ""))]
    assert upload[0]["with"]["path"] == "_site"


def test_docs_site_has_a_landing_page_linking_the_guides() -> None:
    """The site root answers with an index that links both guides and the CIMD document."""
    index = (DOCS / "index.md").read_text()
    assert "user-guide.md" in index
    assert "library.md" in index
    assert "oauth/client.json" in index


def test_docs_site_config_keeps_internal_notes_out() -> None:
    """Internal integration notes are not published; the CIMD JSON is a static file that
    Jekyll copies unchanged (no front matter, not excluded)."""
    config = yaml.safe_load((DOCS / "_config.yml").read_text())
    assert "integration-prompts.md" in config["exclude"]
    assert not any("oauth" in str(entry) for entry in config["exclude"])
    assert not (DOCS / "oauth" / "client.json").read_text().startswith("---")
