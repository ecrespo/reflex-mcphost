# Contributing to reflex-mcphost

Read `specs/constitution.md` first; its articles are non-negotiable. No implementation
without an approved spec (Art. 9). Code, comments, docs and commit messages are in English;
commits follow Conventional Commits.

## Environment

`uv` is the only supported tool (Tech Design DD-015):

```bash
uv sync --all-extras --all-groups
uv run pre-commit install
```

## Quality gate (Art. 8)

| Check | Command |
|---|---|
| Lint | `uv run ruff check` |
| Format | `uv run ruff format --check` |
| Types | `uv run mypy` (strict, `src/`) |
| Architecture | `uv run lint-imports` |
| Tests | `uv run pytest` |
| Secrets | `uv run pre-commit run gitleaks --all-files` |
| REQ coverage | `uv run python scripts/check_req_coverage.py` |
| Wheel contents | `uv build && uv run python scripts/check_wheel.py dist/*.whl` |

Every test docstring starts with the REQ IDs or spec section it verifies
(`"""REQ-REG-012: …"""`), so `scripts/check_req_coverage.py` can trace it (Art. 7).
