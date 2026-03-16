"""Ensure tests always run against a known config.toml, never a real one."""

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
    """Always write the test config.toml, saving any pre-existing one."""
    config._cardea_original_config = (
        CONFIG_PATH.read_text() if CONFIG_PATH.exists() else None
    )
    CONFIG_PATH.write_text(_TEST_CONFIG)


def pytest_unconfigure(config):
    """Restore the original config.toml, or remove ours if there was none."""
    original = getattr(config, "_cardea_original_config", None)
    if original is not None:
        CONFIG_PATH.write_text(original)
    elif CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
