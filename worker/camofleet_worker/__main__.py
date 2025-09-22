"""Entrypoint for ``python -m camofleet_worker``."""

from __future__ import annotations

import logging

import uvicorn

from .config import load_settings
from .main import create_app


def main() -> None:
    settings = load_settings()
    logging.getLogger("camofleet_worker").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        timeout_keep_alive=20,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
