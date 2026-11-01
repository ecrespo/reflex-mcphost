"""Config source discovery and precedence (PRD RF-REG, DD-013)."""

from __future__ import annotations

from pathlib import Path

import pytest

from reflex_mcphost.core.config.models import SourceKind, SourcesConfig
from reflex_mcphost.core.config.paths import Platform, discover_sources

HOME = Path("/home/me")
CWD = Path("/work/proj")
USER_FILE = Path("/cfg/reflex-mcphost/mcphost.toml")


def _linux() -> Platform:
    return Platform(system="linux", home=HOME, env={})


def test_total_order_without_extra_files() -> None:
    """REQ-REG-001: sources follow the RF-REG total order, 1 = highest."""
    specs = discover_sources(CWD, user_file=USER_FILE, sources=SourcesConfig(), platform=_linux())
    assert [(s.precedence, s.kind) for s in specs] == [
        (1, SourceKind.MCPHOST_PROJECT),
        (2, SourceKind.MCPHOST_USER),
        (3, SourceKind.MCP_JSON_PROJECT),
        (4, SourceKind.CURSOR_PROJECT),
        (5, SourceKind.VSCODE_PROJECT),
        (6, SourceKind.CURSOR_USER),
        (7, SourceKind.VSCODE_USER),
        (8, SourceKind.CLAUDE_DESKTOP),
    ]
    assert specs[0].path == CWD / "mcphost.toml"
    assert specs[1].path == USER_FILE
    assert specs[2].path == CWD / ".mcp.json"
    assert specs[3].path == CWD / ".cursor" / "mcp.json"
    assert specs[4].path == CWD / ".vscode" / "mcp.json"


@pytest.mark.parametrize("n", [1, 3])
def test_extra_files_take_consecutive_precedence(n: int) -> None:
    """REQ-REG-001: `extra` files sit after project files, in list order."""
    extra = tuple(f"extra{i}.json" for i in range(n))
    specs = discover_sources(
        CWD, user_file=USER_FILE, sources=SourcesConfig(extra=extra), platform=_linux()
    )
    extras = [s for s in specs if s.kind is SourceKind.EXTRA]
    assert [s.precedence for s in extras] == list(range(6, 6 + n))
    # relative extra paths resolve against the user config file's directory
    assert [s.path for s in extras] == [USER_FILE.parent / e for e in extra]
    assert specs[-1].kind is SourceKind.CLAUDE_DESKTOP
    assert specs[-1].precedence == 8 + n


def test_disabled_sources_are_skipped_without_renumbering() -> None:
    """REQ-REG-001: precedence is positional in the total order, not renumbered."""
    sources = SourcesConfig(claude_desktop=False, cursor=False, project_mcp_json=False)
    specs = discover_sources(CWD, user_file=USER_FILE, sources=sources, platform=_linux())
    assert [(s.precedence, s.kind) for s in specs] == [
        (1, SourceKind.MCPHOST_PROJECT),
        (2, SourceKind.MCPHOST_USER),
        (5, SourceKind.VSCODE_PROJECT),
        (7, SourceKind.VSCODE_USER),
    ]


def test_extra_home_relative_path_is_expanded() -> None:
    """REQ-REG-001: `~` in an extra path is the user's home."""
    specs = discover_sources(
        CWD, user_file=USER_FILE, sources=SourcesConfig(extra=("~/x.json",)), platform=_linux()
    )
    assert [s.path for s in specs if s.kind is SourceKind.EXTRA] == [HOME / "x.json"]


@pytest.mark.parametrize(
    ("platform", "claude", "cursor", "vscode"),
    [
        (
            Platform(system="linux", home=HOME, env={}),
            HOME / ".config/Claude/claude_desktop_config.json",
            HOME / ".cursor/mcp.json",
            HOME / ".config/Code/User/mcp.json",
        ),
        (
            Platform(system="darwin", home=HOME, env={}),
            HOME / "Library/Application Support/Claude/claude_desktop_config.json",
            HOME / ".cursor/mcp.json",
            HOME / "Library/Application Support/Code/User/mcp.json",
        ),
        (
            Platform(
                system="windows",
                home=Path("/Users/me"),
                env={"APPDATA": "/AppData/Roaming", "USERPROFILE": "/Users/me"},
            ),
            Path("/AppData/Roaming") / "Claude" / "claude_desktop_config.json",
            Path("/Users/me") / ".cursor" / "mcp.json",
            Path("/AppData/Roaming") / "Code" / "User" / "mcp.json",
        ),
    ],
)
def test_user_level_paths_per_os(
    platform: Platform, claude: Path, cursor: Path, vscode: Path
) -> None:
    """REQ-REG-001: DD-013 per-OS paths of third-party user configs."""
    specs = {
        s.kind: s.path
        for s in discover_sources(
            CWD, user_file=USER_FILE, sources=SourcesConfig(), platform=platform
        )
    }
    assert specs[SourceKind.CLAUDE_DESKTOP] == claude
    assert specs[SourceKind.CURSOR_USER] == cursor
    assert specs[SourceKind.VSCODE_USER] == vscode


def test_current_platform_is_detected() -> None:
    """REQ-REG-001: `Platform.current()` reflects the running OS."""
    current = Platform.current()
    assert current.system in {"linux", "darwin", "windows"}
    assert current.home == Path.home()
