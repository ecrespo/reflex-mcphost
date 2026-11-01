"""README / ``docs/`` code blocks are checked, and the runnable ones run (F5-05, F8-05).

A Python block may be preceded by a marker comment that says how it is checked:

- ``<!-- snippet: verbatim <test file> -->`` — every code line (imports excluded) appears
  verbatim in that integration test, which drives it against the fixture Servers.
- ``<!-- snippet: run -->`` — the block is a complete script; it runs in a subprocess with
  an isolated home whose user ``mcphost.toml`` defines a Server ``filesystem`` (a minimal
  stdio fixture with ``read_text_file``), so ``Host()`` reads no real config.
- ``<!-- snippet: run model=TestModel -->`` — as ``run``, with the block's one
  ``"anthropic:…"`` model string replaced by PydanticAI's ``TestModel()`` (no API key).
- ``<!-- snippet: rxconfig -->`` — a Reflex config that ``reflex_mcphost.ui`` accepts.
- ``<!-- snippet: embed -->`` — an app module; it runs and installs the Host.

Every other Python block must compile (top-level ``await`` allowed: fragments) and its
imports must resolve.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import platformdirs
import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]
_BLOCK = re.compile(r"(?:<!-- snippet: (?P<marker>[^>]*?) -->\n)?```python\n(?P<code>.*?)```", re.S)
MODEL = re.compile(r'"anthropic:[\w.-]+"')
FS_SERVER = textwrap.dedent(
    '''
    """Minimal stdio fixture for the README snippets (test-only code)."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("filesystem")


    @server.tool()
    def read_text_file(path: str) -> str:
        """Read a text file."""
        return f"hello from {path}"


    if __name__ == "__main__":
        server.run()
    '''
)


def _blocks() -> list[tuple[str, str, str]]:
    """``(doc name, marker, code)`` of every Python block in README and ``docs/``."""
    found = []
    for doc in DOCS:
        for m in _BLOCK.finditer(doc.read_text(encoding="utf-8")):
            found.append((doc.name, (m["marker"] or "").strip(), m["code"]))
    return found


def _marked(prefix: str) -> list[tuple[str, str, str]]:
    return [b for b in _blocks() if b[1].split(" ")[0] == prefix]


def _id(block: tuple[str, str, str]) -> str:
    return f"{block[0]}:{block[2].splitlines()[0][:40]}"


def test_snippet_markers_are_known() -> None:
    """Art. 9, REQ-UI-007: the runnable docs examples are marked (both README scripts, the
    two adapter snippets and the embedder config), and no marker is misspelt."""
    markers = [marker for _, marker, _ in _blocks() if marker]
    kinds = {"verbatim", "run", "rxconfig", "embed"}
    assert all(m.split(" ")[0] in kinds for m in markers), markers
    assert len(_marked("run")) == 2
    assert len(_marked("verbatim")) == 2
    assert len(_marked("rxconfig")) == 1
    assert len(_marked("embed")) == 1


@pytest.mark.parametrize("block", _blocks(), ids=_id)
def test_every_python_block_compiles_and_imports(block: tuple[str, str, str]) -> None:
    """Art. 9, REQ-UI-007: every Python block in README / ``docs/`` is valid Python and
    imports only names that exist (catches renamed public API in the docs)."""
    _, _, code = block
    tree = compile(code, "<doc>", "exec", ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]
    exec(compile(ast.Module(body=imports, type_ignores=[]), "<doc>", "exec"), {})  # noqa: S102 - our docs


@pytest.mark.parametrize("block", _marked("verbatim"), ids=_id)
def test_snippet_lines_are_in_the_integration_test(block: tuple[str, str, str]) -> None:
    """REQ-TLS-006, REQ-TLS-007: every code line of an adapter snippet (imports excluded)
    appears verbatim in the integration test that drives it against the fixture Servers."""
    _, marker, code = block
    source = (REPO / marker.split(" ", 1)[1]).read_text(encoding="utf-8")
    test_lines = {line.strip() for line in source.splitlines()}
    lines = [
        line.strip()
        for line in code.splitlines()
        if line.strip() and not line.lstrip().startswith(("from ", "import ", "#"))
    ]
    assert lines
    assert [line for line in lines if line not in test_lines] == []


def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Environment for a subprocess whose ``Host()`` finds only our ``filesystem``."""
    home = tmp_path / "home"
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",  # 0600 file fallback
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    user_file = Path(platformdirs.user_config_dir("reflex-mcphost", appauthor=False))
    user_file.mkdir(parents=True)
    server = tmp_path / "fs_server.py"
    server.write_text(FS_SERVER, encoding="utf-8")
    (user_file / "mcphost.toml").write_text(
        "schema_version = 1\n\n[servers.filesystem]\n"
        f'transport = "stdio"\ncommand = {sys.executable!r}\nargs = [{str(server)!r}]\n'.replace(
            "'", '"'
        ),
        encoding="utf-8",
    )
    (tmp_path / "work").mkdir()
    keep = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "TMPDIR", "LANG"}}
    return {**keep, **env}


@pytest.mark.skipif(sys.platform == "win32", reason="isolated home via HOME / XDG (POSIX)")
@pytest.mark.parametrize("block", _marked("run"), ids=_id)
def test_runnable_snippet_runs(
    block: tuple[str, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-INV-011, REQ-TLS-006, REQ-CNS-014 (F8-05): the README's library and agent scripts
    run unchanged (the agent with ``TestModel`` instead of the real model string) against a
    ``filesystem`` Server: the tool call succeeds and the audit log lists it."""
    _, marker, code = block
    if marker == "run model=TestModel":
        assert len(MODEL.findall(code)) == 1
        code = "from pydantic_ai.models.test import TestModel\n" + MODEL.sub("TestModel()", code)
    script = tmp_path / "snippet.py"
    script.write_text(code, encoding="utf-8")
    env = _isolated_home(tmp_path, monkeypatch)
    done = subprocess.run(  # noqa: S603 - our interpreter, our script
        [sys.executable, str(script)],
        cwd=tmp_path / "work",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "hello from" in done.stdout


def test_rxconfig_snippet_is_accepted_by_the_ui() -> None:
    """REQ-UI-007, REQ-NFR-011, API §8 (F8-05): the embedder config of ``docs/library.md``
    uses the MEMORY state manager and a ``deploy_url`` the guard accepts without remote
    mode."""
    rx = pytest.importorskip("reflex")
    from reflex_mcphost.ui.install import allowed_origin

    ((_, _, code),) = _marked("rxconfig")
    namespace: dict[str, object] = {}
    exec(code, namespace)  # noqa: S102 - our docs
    config = namespace["config"]
    assert config.state_manager_mode == rx.constants.StateManagerMode.MEMORY  # type: ignore[attr-defined]
    origin = allowed_origin(config.deploy_url, allow_remote=False)  # type: ignore[attr-defined]
    assert (origin.host, origin.port) == ("127.0.0.1", 3000)


def test_embed_snippet_installs_the_host() -> None:
    """REQ-UI-007, API §8 (F8-05): the embedder app module of ``docs/library.md`` runs: its
    page uses ``HostState.start_watch`` and ``install`` registers the ``Host`` factory."""
    pytest.importorskip("reflex")
    from reflex_base.registry import RegistrationContext

    from reflex_mcphost import Host

    ((_, _, code),) = _marked("embed")
    assert "on_load=HostState.start_watch" in code
    namespace: dict[str, object] = {}
    with RegistrationContext.ensure_context().fork():
        exec(code, namespace)  # noqa: S102 - our docs
    assert namespace["app"]._reflex_mcphost_host_factory is Host  # type: ignore[attr-defined]
