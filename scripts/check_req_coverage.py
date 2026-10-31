"""REQ coverage (Constitution Art. 7): every MUST criterion is cited by a test docstring.

Priorities follow the PRD §6 priority rule: a criterion's trailing tag overrides its group
or sub-group; an untagged criterion takes its group's (or sub-group's) priority; all
REQ-NFR are MUST. Report-only by default; ``--strict`` exits 1 on any uncovered MUST.
``--families REG,CON,CAT`` limits the report (and the strict gate) to those REQ families.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REQ_RE = re.compile(r"\bREQ-[A-Z]+-\d{3}\b")
TAG_RE = re.compile(r"`(MUST|SHOULD|COULD)`")
TRAILING_RE = re.compile(r"—\s*`(MUST|SHOULD|COULD)`[^`|]*$")
ROW_RE = re.compile(r"^\|\s*(REQ-[A-Z]+-\d{3})\s*\|[^|]*\|(.*)\|\s*$")
NFR_RE = re.compile(r"^\s*-\s*(REQ-NFR-\d{3})\b")
SUBGROUP_RE = re.compile(r"^\*\*[^*]+\*\*\s*—\s*`(MUST|SHOULD|COULD)`")


def parse_priorities(prd: str) -> dict[str, str]:
    priorities: dict[str, str] = {}
    group: str | None = None
    for line in prd.splitlines():
        if line.startswith("### RF-"):
            tag = TAG_RE.search(line)
            group = tag.group(1) if tag else None
            continue
        if line.startswith("## "):
            group = None
            continue
        sub = SUBGROUP_RE.match(line)
        if sub:
            group = sub.group(1)
            continue
        nfr = NFR_RE.match(line)
        if nfr:
            priorities[nfr.group(1)] = "MUST"
            continue
        row = ROW_RE.match(line)
        if row:
            req, criterion = row.group(1), row.group(2).strip()
            trailing = TRAILING_RE.search(criterion)
            prio = trailing.group(1) if trailing else group
            if prio is None:
                raise ValueError(f"{req}: no priority (no tag and no group priority)")
            priorities[req] = prio
    return priorities


def cited_reqs(tests_dir: Path) -> set[str]:
    cited: set[str] = set()
    for path in sorted(tests_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                doc = ast.get_docstring(node)
                if doc:
                    cited.update(REQ_RE.findall(doc))
    return cited


def family(req: str) -> str:
    """``REQ-CAT-001`` → ``CAT``."""
    return req.split("-")[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prd", type=Path, nargs="?", default=Path("specs/prd/mcphost-v0.1.md"))
    parser.add_argument("tests", type=Path, nargs="?", default=Path("tests"))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--report-only", action="store_true", help="default; kept for CI")
    parser.add_argument(
        "--families", default=None, help="comma-separated REQ families, e.g. REG,CON,CAT"
    )
    args = parser.parse_args(argv)
    priorities = parse_priorities(args.prd.read_text(encoding="utf-8"))
    must = {req for req, prio in priorities.items() if prio == "MUST"}
    scope = ""
    if args.families:
        wanted = {f.strip().upper() for f in args.families.split(",") if f.strip()}
        unknown = sorted(wanted - {family(req) for req in priorities})
        if unknown:
            parser.error(f"unknown REQ families: {', '.join(unknown)}")
        must = {req for req in must if family(req) in wanted}
        scope = f" (families {', '.join(sorted(wanted))})"
    uncovered = sorted(must - cited_reqs(args.tests))
    print(f"MUST criteria{scope}: {len(must)}; covered: {len(must) - len(uncovered)}")
    for req in uncovered:
        print(f"uncovered: {req}")
    return 1 if args.strict and uncovered else 0


if __name__ == "__main__":
    sys.exit(main())
