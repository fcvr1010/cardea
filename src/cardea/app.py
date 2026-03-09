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


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.warning(
            "No config.toml found — copy config.toml.example to config.toml "
            "and enable the modules you need. No modules will be loaded."
        )
        return {}
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


app = FastAPI(
    title="Cardea",
    description="Credential-injecting reverse proxy for coding assistants",
    version="0.1.0",
)

# ── Auto-discover and mount proxy modules ────────────────────────────────────
_config = _load_config()
modules: dict[str, bool] = _config.get("modules", {})
loaded = 0
_disabled_endpoints: set[str] = set()

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

    # Collect per-module disabled endpoints.
    module_section = _config.get(name, {})
    for ep in module_section.get("disabled_endpoints", []):
        full_path = f"{prefix}/{ep.lstrip('/')}"
        _disabled_endpoints.add(full_path)
        logger.info("Endpoint disabled by config: %s", full_path)

if not loaded:
    logger.warning("No modules enabled — Cardea is running but won't proxy anything.")

if _disabled_endpoints:

    @app.middleware("http")
    async def _block_disabled_endpoints(request, call_next):  # type: ignore[misc]
        if request.url.path in _disabled_endpoints:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        f"Endpoint {request.url.path} is disabled by configuration."
                    )
                },
            )
        return await call_next(request)


@app.get("/health", tags=["Meta"])
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
