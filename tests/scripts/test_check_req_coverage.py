"""Constitution Art. 7: every MUST criterion is cited by at least one test."""

from __future__ import annotations

from pathlib import Path

from scripts.check_req_coverage import cited_reqs, main, parse_priorities

PRD = """\
## 6. Functional requirements

### RF-AAA: Alpha — `MUST`

| ID | Type | Criterion |
|---|---|---|
| REQ-AAA-001 | event | WHEN x, THE SYSTEM SHALL y. |
| REQ-AAA-002 | event | WHEN x, THE SYSTEM SHALL — legacy: y. — `SHOULD` |

### RF-BBB: Beta — sub `MUST` · other `SHOULD`

**Sub** — `MUST`

| ID | Type | Criterion |
|---|---|---|
| REQ-BBB-001 | event | WHEN x, THE SYSTEM SHALL y. |

**Other** — `SHOULD`

| ID | Type | Criterion |
|---|---|---|
| REQ-BBB-010 | event | WHEN x, THE SYSTEM SHALL y. |
| REQ-BBB-011 | event | THE SYSTEM SHALL z. — `MUST` (security invariant) |

## 7. Non-functional requirements

- REQ-NFR-001: Something SHALL be fast.
"""


def _write_prd(tmp_path: Path) -> Path:
    prd = tmp_path / "prd.md"
    prd.write_text(PRD, encoding="utf-8")
    return prd


def test_priority_rule(tmp_path: Path) -> None:
    """PRD §6 priority rule: trailing tag overrides group/sub-group; NFRs are MUST."""
    prio = parse_priorities(_write_prd(tmp_path).read_text(encoding="utf-8"))
    assert prio == {
        "REQ-AAA-001": "MUST",
        "REQ-AAA-002": "SHOULD",
        "REQ-BBB-001": "MUST",
        "REQ-BBB-010": "SHOULD",
        "REQ-BBB-011": "MUST",
        "REQ-NFR-001": "MUST",
    }


def test_cited_reqs_reads_docstrings_only(tmp_path: Path) -> None:
    """Art. 7: citations come from test docstrings, not from comments or strings."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        '"""Module cites REQ-AAA-001."""\n'
        "# REQ-BBB-001 in a comment does not count\n"
        "def test_a():\n"
        '    """REQ-NFR-001, REQ-BBB-011."""\n'
        '    x = "REQ-BBB-010"\n',
        encoding="utf-8",
    )
    assert cited_reqs(tests) == {"REQ-AAA-001", "REQ-NFR-001", "REQ-BBB-011"}


def test_strict_fails_on_uncovered_must(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Art. 7: --strict exits 1 and lists every uncovered MUST."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text('def test_a():\n    """REQ-AAA-001."""\n', encoding="utf-8")
    prd = _write_prd(tmp_path)
    assert main([str(prd), str(tests), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "REQ-BBB-001" in out
    assert "REQ-NFR-001" in out
    assert "REQ-AAA-002" not in out  # SHOULD is not gated
    assert main([str(prd), str(tests)]) == 0  # report-only by default


def test_real_prd_parses(tmp_path: Path) -> None:
    """Art. 7: the real PRD yields 152 REQs and honours trailing-tag promotion."""
    prio = parse_priorities(Path("specs/prd/mcphost-v0.1.md").read_text(encoding="utf-8"))
    assert len(prio) == 152
    assert prio["REQ-HSH-022"] == "MUST"
    assert prio["REQ-HSH-020"] == "SHOULD"
    assert prio["REQ-CON-013"] == "COULD"
