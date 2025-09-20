"""Entrypoint for ``python -m camofleet_worker``."""

from __future__ import annotations

import uvicorn

from .config import load_settings
from .main import create_app


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        timeout_keep_alive=20,
    )


if __name__ == "__main__":
    main()
