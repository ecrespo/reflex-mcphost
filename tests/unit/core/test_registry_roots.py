"""``ServerRegistry.set_roots`` (F4-03; API §4.5 ``set_roots``, REQ-REG-009/010, Art. 6).
*Roots are deprecated in the pinned spec (SEP-2577); compatibility feature.*"""

from __future__ import annotations

import pytest

from reflex_mcphost.core.config.models import HostConfig, RegistryDiff, SourceKind
from reflex_mcphost.core.errors import UnknownServerError
from tests.conftest import ThirdPartyFiles
from tests.unit.core.test_registry_mutations import ENV, Recorder, make_registry

pytestmark = pytest.mark.anyio


async def test_own_server_roots_written_to_its_table(third_party_files: ThirdPartyFiles) -> None:
    """REQ-HSH-022, REQ-REG-009: an own Server's roots are written to its own table; the
    roots-only change reconciles as unchanged (no reconnect diff)."""
    tp = third_party_files
    registry = await make_registry(tp)
    recorder = Recorder()
    registry.on_diff.append(recorder)
    await registry.set_roots("mine", ("file:///home/me/work",))
    assert registry.get("mine").roots == ("file:///home/me/work",)
    config = HostConfig.load(tp.user_file, cwd=tp.cwd, env=ENV)
    assert config.servers["mine"].roots == ("file:///home/me/work",)
    assert config.overrides == {}
    assert "# my settings — keep this comment" in tp.user_file.read_text(encoding="utf-8")
    assert recorder.diffs == [RegistryDiff(unchanged=("mine",))]
    await registry.set_roots("mine", ())
    assert registry.get("mine").roots == ()
    assert "roots" not in tp.user_file.read_text(encoding="utf-8")


async def test_third_party_roots_go_to_overrides(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-010, Art. 6: a third-party Server's roots go to [overrides.<id>]; the
    third-party file stays byte-identical and nothing reconnects."""
    tp = third_party_files
    tp.install(SourceKind.CLAUDE_DESKTOP)
    registry = await make_registry(tp)
    recorder = Recorder()
    registry.on_diff.append(recorder)
    await registry.set_roots("fs", ["file:///srv/data"])
    assert registry.get("fs").roots == ("file:///srv/data",)
    assert registry.get("fs").origin.kind is SourceKind.CLAUDE_DESKTOP
    config = HostConfig.load(tp.user_file, cwd=tp.cwd, env=ENV)
    assert config.overrides["fs"].roots == ("file:///srv/data",)
    assert all(not diff.changed for diff in recorder.diffs)
    await registry.set_roots("fs", [])
    assert registry.get("fs").roots == ()
    tp.verify()


async def test_set_roots_validates_and_knows_the_server(third_party_files: ThirdPartyFiles) -> None:
    """REQ-HSH-022: non-file:// roots raise ValueError and nothing is written; unknown ids
    raise UnknownServerError."""
    tp = third_party_files
    registry = await make_registry(tp)
    before = tp.user_file.read_bytes()
    with pytest.raises(ValueError, match="file://"):
        await registry.set_roots("mine", ["https://x"])
    with pytest.raises(UnknownServerError):
        await registry.set_roots("ghost", ["file:///x"])
    assert tp.user_file.read_bytes() == before


async def test_other_changes_still_reconnect(third_party_files: ThirdPartyFiles) -> None:
    """REQ-REG-013: a change beyond roots is still reported as changed."""
    tp = third_party_files
    registry = await make_registry(tp)
    recorder = Recorder()
    registry.on_diff.append(recorder)
    await registry.set_enabled("mine", False)
    assert recorder.diffs[-1].changed == ("mine",)
