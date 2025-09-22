"""Entrypoint for ``python -m camoufox_runner``."""

from __future__ import annotations

import logging

import uvicorn

from .config import load_settings
from .main import create_app


def main() -> None:
    settings = load_settings()
    logging.getLogger("camoufox_runner").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
