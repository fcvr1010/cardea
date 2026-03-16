"""Create a config.toml with all modules enabled for testing.

WARNING: If a config.toml already exists in the repo root (e.g. from local
development), the test fixtures will NOT overwrite it.  Tests will silently
run against that real configuration instead of the test config defined below,
which may cause unexpected passes or failures.  To ensure deterministic test
runs, remove or rename your local config.toml before running the test suite.
"""

from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

_TEST_CONFIG = """\
[modules]
telegram = true
gmail = true
email = true

[email]
address = "test@example.com"
imap_server = "imap.example.com"
smtp_server = "smtp.example.com"

[services.github-api]
prefix = "/github/api"
upstream = "https://api.github.com"
auth = { type = "bearer", secret = "cardea_github_token" }

[services.github-git]
prefix = "/github"
upstream = "https://github.com"
auth = { type = "basic", username = "x-access-token", secret = "cardea_github_token" }
"""


def pytest_configure(config):
    """Write an all-enabled config.toml if one doesn't already exist."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(_TEST_CONFIG)
        config._cardea_created_config = True
    else:
        config._cardea_created_config = False


def pytest_unconfigure(config):
    """Remove the config.toml we created, if any."""
    if getattr(config, "_cardea_created_config", False) and CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
