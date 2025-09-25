"""Entrypoint for running the VNC gateway with ``python -m``."""

from __future__ import annotations

import uvicorn

from .app import create_app
from .config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.http_host,
        port=settings.http_port,
        log_level="info",
        loop="asyncio",
    )


if __name__ == "__main__":
    main()
