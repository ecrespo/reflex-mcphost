"""TD §9.1, DD-015, delta D14 (F8-07a): static checks of the post-publish ``verify`` job of
``release.yml`` and of the release path around it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

RELEASE = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def jobs() -> dict[str, Any]:
    data = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _script(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_verify_runs_after_publish_on_three_os(jobs: dict[str, Any]) -> None:
    """TD §9.1, REQ-NFR-030: ``verify`` needs ``publish`` (so it runs only for a published
    ``v*`` tag), on Linux, macOS and Windows with Python 3.13, without fail-fast."""
    verify = jobs["verify"]
    needs = verify["needs"]
    assert "publish" in ([needs] if isinstance(needs, str) else needs)
    matrix = verify["strategy"]["matrix"]
    assert set(matrix["os"]) == {"ubuntu-latest", "macos-latest", "windows-latest"}
    assert [str(v) for v in matrix["python"]] == ["3.13"]
    assert verify["strategy"]["fail-fast"] is False
    assert verify["runs-on"] == "${{ matrix.os }}"


def test_verify_holds_no_id_token(jobs: dict[str, Any]) -> None:
    """TD §9.1, DD-015: ``verify`` holds no ``id-token`` (only ``publish`` may upload) and no
    write permission at all; it checks out no source (it tests what PyPI serves)."""
    verify = jobs["verify"]
    permissions = verify.get("permissions", {})
    assert "id-token" not in permissions
    assert all(value != "write" for value in permissions.values())
    assert "environment" not in verify
    uses = [str(step.get("uses", "")) for step in verify["steps"]]
    assert not [u for u in uses if u.startswith("actions/checkout@")]


def test_verify_installs_from_pypi_and_runs_the_cli(jobs: dict[str, Any]) -> None:
    """TD §9.1: ``verify`` waits (bounded, ≤ 10 min) until the version is on PyPI, then runs
    ``uvx --from reflex-mcphost==<version>`` ``--version`` (must print it) and
    ``servers --json`` on a minimal config; on Linux also ``run --no-browser`` until
    ``GET /ping`` answers."""
    verify = jobs["verify"]
    script = _script(verify)
    assert "pypi.org" in script
    assert "600" in script  # the wait is bounded at 10 minutes
    assert 'reflex-mcphost=="$VERSION"' in script or "reflex-mcphost==$VERSION" in script
    assert "--version" in script
    assert '[ "$out" = "reflex-mcphost $VERSION" ]' in script
    assert "servers --json --config" in script
    linux = [s for s in verify["steps"] if "run --no-browser" in str(s.get("run", ""))]
    assert len(linux) == 1
    assert linux[0]["if"] == "runner.os == 'Linux'"
    assert "/ping" in linux[0]["run"]
    assert verify["env"]["VERSION"] == "${{ needs.build.outputs.version }}"
    assert "build" in verify["needs"]


def test_verify_disables_the_setup_uv_cache(jobs: dict[str, Any]) -> None:
    """TD §9.1: ``verify`` runs with ``UV_NO_CACHE`` (never a cached pre-upload resolution),
    so setup-uv's cache must be off too; otherwise its post-job save fails on the empty cache
    path and marks a passing verification red (seen on windows-latest, v0.1.0)."""
    verify = jobs["verify"]
    assert verify["env"]["UV_NO_CACHE"] == "1"
    setup = [s for s in verify["steps"] if "astral-sh/setup-uv" in str(s.get("uses", ""))]
    assert setup, "verify must install uv"
    for step in setup:
        assert step.get("with", {}).get("enable-cache") is False


def test_publish_stays_the_only_uploader(jobs: dict[str, Any]) -> None:
    """DD-015: only ``publish`` uploads (``uv publish`` without ``--dry-run``) and holds
    ``id-token: write``; it is tag-gated and runs in environment ``pypi``."""
    uploaders = [
        name
        for name, job in jobs.items()
        if "uv publish" in _script(job) and "--dry-run" not in _script(job)
    ]
    assert uploaders == ["publish"]
    holders = [n for n, j in jobs.items() if j.get("permissions", {}).get("id-token")]
    assert holders == ["publish"]
    publish = jobs["publish"]
    assert "startsWith(github.ref, 'refs/tags/v')" in publish["if"]
    assert publish["environment"]["name"] == "pypi"


CHANGELOG = RELEASE.parents[2] / "CHANGELOG.md"
P8_24 = {
    "JSON Schema `format`": "format",
    "token expiry after restart": "Token expiry is unknown after a restart",
    "re-prompt card": "re-prompts an elicitation",
    "media allow-list": "Server media in results",
    "host-context-changed": "host-context-changed",
    "E2E Linux-only": "runs on Linux only",
    "REQ-CON-013 deferred": "REQ-CON-013",
    "View self-navigation residual": "navigates its own frame away",
    "real Safari status": "Real Safari",
    "S-4 status": "spike S-4",
}
"""Analyze P8-24 / F8-07a: the known limitations ``0.1.0`` must list."""


def test_changelog_0_1_0_lists_the_known_limitations() -> None:
    """Art. 9, F8-07a (Analyze P8-24): ``CHANGELOG.md`` has a dated ``[0.1.0]`` section
    (below ``[Unreleased]``, which collects post-release changes) whose known limitations
    name every accepted gap; the release PR (F8-07b) sets the version to ``0.1.0``."""
    import tomllib

    text = CHANGELOG.read_text(encoding="utf-8")
    assert "## [0.1.0] - 2026-11-01\n" in text
    assert text.index("## [Unreleased]") < text.index("## [0.1.0]")
    section = text[text.index("## [0.1.0]") :]
    limits = section[section.index("### Known limitations") :]
    missing = [name for name, needle in P8_24.items() if needle not in limits]
    assert missing == []
    pyproject = tomllib.loads((RELEASE.parents[2] / "pyproject.toml").read_text("utf-8"))
    assert pyproject["project"]["version"] == "0.1.0"
