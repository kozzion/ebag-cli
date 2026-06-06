# CLAUDE.md

Project-specific guidance for Claude Code working in this repository.

## What this is

A Python command-line tool called `ebag`. Early scaffolding stage — there is
no implementation yet; only this file, a `README.md`, and a Python
`.gitignore`. The exact purpose of the tool is still TBD; update this section
once it's defined.

## Stack & conventions

- **Language:** Python 3.11+
- **Style:** PEP 8; format with `ruff format`; lint with `ruff check`
- **Type hints:** use them on all public functions and CLI entry points
- **Tests:** `pytest`
- **CLI framework:** to be decided (likely `typer` or `argparse` — pick one
  before adding the first command)
- **Dependency management:** start with `requirements.txt`; switch to
  `pyproject.toml` + `uv`/`pip-tools` once dependencies are non-trivial

## Repo layout (target)

```
ebag-cli/
├── ebag_cli/             # Importable package
│   ├── __init__.py
│   └── cli.py            # CLI entry point
├── tests/
│   └── test_cli.py
├── requirements.txt
├── README.md
├── CLAUDE.md
└── .gitignore
```

## When adding code

- Put the entry point under `ebag_cli/cli.py` and expose it via
  `pyproject.toml` console-script (e.g. `ebag = "ebag_cli.cli:main"`).
- Keep network / external-service calls in a separate module from the CLI
  parsing so the underlying logic can be unit-tested without spawning a
  subprocess.
- Never commit secrets (API keys, tokens). Read them from environment
  variables and document the required names in the README.
- Add a corresponding `tests/test_*.py` for every non-trivial change.

## Commits & PRs

- Conventional commit prefixes are nice but not required: `feat:`, `fix:`,
  `chore:`, `docs:`, `test:`.
- One logical change per commit.
- Run `ruff check .` and `pytest` before pushing.

## Owner

`kozzion` (Jaap Oosterbroek).
