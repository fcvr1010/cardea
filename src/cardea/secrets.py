import os
from pathlib import Path


def get_secret(name: str) -> str:
    """Read a secret from /run/secrets/<name>, falling back to env var."""
    secret_path = Path(f"/run/secrets/{name}")
    if secret_path.exists():
        return secret_path.read_text().strip()
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(
            f"Secret '{name}' not found — provide it as a file in "
            f"/run/secrets/{name} or as an environment variable."
        )
    return value
