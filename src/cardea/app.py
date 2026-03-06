"""
Cardea — credential-injecting reverse proxy

A local reverse proxy that injects credentials on behalf of coding assistants,
so that real secrets never appear in .env files or environment variables visible
to the assistant.
"""

import importlib
import logging
import pkgutil
import tomllib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import cardea.proxies

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.toml"


def _load_modules_config() -> dict[str, bool]:
    if not CONFIG_PATH.exists():
        logger.warning(
            "No config.toml found — copy config.toml.example to config.toml "
            "and enable the modules you need. No modules will be loaded."
        )
        return {}
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    modules: dict[str, bool] = config.get("modules", {})
    return modules


app = FastAPI(
    title="Cardea",
    description="Credential-injecting reverse proxy for coding assistants",
    version="0.1.0",
)

# ── Auto-discover and mount proxy modules ────────────────────────────────────
modules = _load_modules_config()
loaded = 0

for finder, name, _ in pkgutil.iter_modules(cardea.proxies.__path__):
    if not modules.get(name):
        continue
    module = importlib.import_module(f"cardea.proxies.{name}")
    router = getattr(module, "router", None)
    if router is None:
        logger.warning("Module %s has no router — skipping", name)
        continue
    prefix = getattr(module, "PREFIX", f"/{name}")
    tag = getattr(module, "TAG", name.capitalize())
    app.include_router(router, prefix=prefix, tags=[tag])
    logger.info("Module enabled: %s (prefix=%s)", name, prefix)
    loaded += 1

if not loaded:
    logger.warning("No modules enabled — Cardea is running but won't proxy anything.")


@app.get("/health", tags=["Meta"])
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
