# Contributing to Cardea

Thank you for your interest in contributing to Cardea. This guide covers the development workflow,
tooling, and conventions you need to follow.

## Development setup

### Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) (package manager)

### Getting started

Clone the repository and install dependencies (including dev tools):

```bash
git clone https://github.com/fcvr1010/cardea.git
cd cardea
uv sync
```

Install pre-commit hooks so that linting and formatting run automatically on every commit:

```bash
uv run pre-commit install
```

## Running tests

Tests use [pytest](https://docs.pytest.org/) with `pytest-asyncio` (async tests run automatically,
no decorator needed) and `pytest-cov` for coverage:

```bash
uv run pytest
```

To run with coverage reporting:

```bash
uv run pytest --cov=cardea --cov-report=term-missing
```

## Running linting

The project enforces code quality with [ruff](https://docs.astral.sh/ruff/) (linting + formatting) and
[mypy](https://mypy-lang.org/) (strict mode type checking).

```bash
# Lint
uv run ruff check src/ tests/

# Format check (no changes, exit non-zero if unformatted)
uv run ruff format --check src/ tests/

# Type check
uv run mypy src/
```

All three checks must pass before a PR can be merged. They also run in CI
(see `.github/workflows/ci.yml`).

## Code style

Code style is enforced automatically -- you rarely need to think about it:

- **ruff** handles both linting and formatting. The pre-commit hook runs `ruff --fix` and
  `ruff-format` on staged files, so most issues are auto-corrected.
- **mypy** runs in strict mode (`strict = true` in `pyproject.toml`). All source code in `src/`
  must have complete type annotations. Test files have relaxed rules
  (`disallow_untyped_defs = false`).
- **markdownlint** checks `.md` files (max line length: 120 characters).
- **shellcheck** lints shell scripts.

If pre-commit hooks are installed (`uv run pre-commit install`), all of these run on every commit.
You can also run them manually across the whole repo:

```bash
uv run pre-commit run --all-files
```

## Custom module development

Cardea has two mechanisms for adding new services:

1. **Config-driven services** -- defined entirely in `config.toml` with no code changes.
   See the [README](README.md#adding-a-new-service) for details.
2. **Custom modules** -- Python modules for services that need custom logic
   (OAuth2 token refresh, non-HTTP protocols, multi-tenant routing, etc.).

This section covers custom modules.

### Module contract

Every custom module is a Python file in `src/cardea/proxies/` that must export three names:

| Export   | Type               | Description                                          |
|----------|--------------------|------------------------------------------------------|
| `router` | `fastapi.APIRouter` | FastAPI router containing the module's endpoints     |
| `PREFIX` | `str`              | URL prefix for mounting (e.g. `"/gmail"`)            |
| `TAG`    | `str`              | OpenAPI tag for grouping endpoints (e.g. `"Gmail"`)  |

### Skeleton example

```python
"""
Short description of what this module proxies.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException

from cardea.secrets import get_secret

logger = logging.getLogger(__name__)

PREFIX = "/my-service"
TAG = "My Service"

router = APIRouter()


@router.get("/items")
async def list_items() -> list[dict[str, str]]:
    token = get_secret("cardea_my_service_token")
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.example.com/items",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]
```

### Key conventions

- **Secrets**: use `cardea.secrets.get_secret(name)` to load credentials.
  It checks `/run/secrets/<name>` first, then falls back to the environment variable.
  Never hard-code credentials.
- **HTTP client**: use `httpx` (already a project dependency).
  For proxying raw requests, see `cardea.proxies._proxy_utils` for shared helpers.
- **Logging**: use `logging.getLogger(__name__)`.
- **Type annotations**: required everywhere (mypy strict mode).
- **Error handling**: raise `fastapi.HTTPException` for client-facing errors.

### Enabling a module

Add the module name (the filename without `.py`) to the `[modules]` section in `config.toml`:

```toml
[modules]
my_service = true
```

Restart Cardea for the module to be loaded.

## Pull request process

1. **Branch** from `main`. Use a descriptive branch name (e.g. `add-calendar-module`,
   `fix-gmail-token-refresh`).
2. **Make your changes.** Keep commits focused and well-described.
3. **Run checks locally** before pushing:

   ```bash
   uv run pre-commit run --all-files
   uv run pytest --cov=cardea
   ```

4. **Push and open a PR** against `main` on GitHub.
5. **CI runs automatically** on every PR (see `.github/workflows/ci.yml`).
   Both the `lint` and `test` jobs must pass.
6. **Code review** is required. A CODEOWNER (`@fcvr1010`) must approve
   before the PR can be merged.

### What reviewers look for

- Correct and complete type annotations (mypy strict).
- Tests for new functionality.
- No hard-coded credentials or secrets.
- Consistent use of existing patterns (see existing modules in `src/cardea/proxies/`
  for reference).
- Clean commit history (squash-merge is the default).
