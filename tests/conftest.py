"""Create a config.toml with all modules enabled for testing."""

from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


def pytest_configure(config):
    """Write an all-enabled config.toml if one doesn't already exist."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            "[modules]\ntelegram = true\ngithub = true\ngmail = true\n"
        )
        config._cardea_created_config = True
    else:
        config._cardea_created_config = False


def pytest_unconfigure(config):
    """Remove the config.toml we created, if any."""
    if getattr(config, "_cardea_created_config", False) and CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
