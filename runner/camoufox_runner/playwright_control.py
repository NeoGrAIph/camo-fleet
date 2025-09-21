"""Management of Playwright-based browser servers for the Camoufox runner."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
from asyncio import subprocess as aio_subprocess
from dataclasses import dataclass
from typing import Any

from camoufox import launch_options
from playwright._impl._driver import compute_driver_executable

from .processes import cancel_tasks, drain_stream, terminate_process

LOGGER = logging.getLogger(__name__)

BROWSER_SERVER_LAUNCH_TIMEOUT = 45


@dataclass(slots=True)
class BrowserServerHandle:
    """Container for a launched browser server process."""

    process: aio_subprocess.Process
    ws_endpoint: str
    drain_tasks: list[asyncio.Task[None]]

    async def close(self) -> None:
        """Terminate the browser server and background log readers."""

        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        await cancel_tasks(self.drain_tasks)


class BrowserServerLauncher:
    """Launch Camoufox-controlled Playwright browser servers."""

    def __init__(self, *, launch_timeout: float = BROWSER_SERVER_LAUNCH_TIMEOUT) -> None:
        self._launch_timeout = launch_timeout

    async def launch(
        self,
        *,
        headless: bool,
        vnc: bool,
        display: str | None,
        override_proxy: dict[str, Any] | None = None,
    ) -> BrowserServerHandle:
        """Launch a Firefox server using Camoufox-provided configuration."""

        opts = launch_options(headless=headless)
        env_vars = {k: v for k, v in (opts.get("env") or {}).items() if v is not None}
        if display:
            env_vars["DISPLAY"] = display
        config: dict[str, Any] = {
            "headless": headless,
            "args": opts.get("args") or [],
            "env": env_vars,
        }
        if executable_path := opts.get("executable_path"):
            config["executablePath"] = executable_path
        if prefs := opts.get("firefox_user_prefs"):
            config["firefoxUserPrefs"] = prefs
        if override_proxy:
            config["proxy"] = override_proxy
        elif proxy := opts.get("proxy"):
            config["proxy"] = proxy
        if opts.get("ignore_default_args") is not None:
            config["ignoreDefaultArgs"] = opts["ignore_default_args"]

        node_path, cli_path = compute_driver_executable()
        config_path = await asyncio.to_thread(_write_launch_config, config)
        process = await aio_subprocess.create_subprocess_exec(
            node_path,
            cli_path,
            "launch-server",
            "--browser=firefox",
            f"--config={config_path}",
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
        )

        try:
            try:
                raw_endpoint = await asyncio.wait_for(
                    process.stdout.readline(), timeout=self._launch_timeout
                )
            except TimeoutError as exc:
                await terminate_process(process)
                raise RuntimeError("Timed out launching Camoufox server") from exc

            if not raw_endpoint:
                stderr_output = await process.stderr.read()
                return_code = await process.wait()
                message = stderr_output.decode().strip() or "unknown error"
                raise RuntimeError(
                    f"Failed to launch Camoufox server (code {return_code}): {message}"
                )

            ws_endpoint = raw_endpoint.decode().strip()
            stdout_task = asyncio.create_task(
                drain_stream(process.stdout, "camoufox-stdout"),
                name="camoufox-server-stdout",
            )
            stderr_task = asyncio.create_task(
                drain_stream(process.stderr, "camoufox-stderr"),
                name="camoufox-server-stderr",
            )
            return BrowserServerHandle(process, ws_endpoint, [stdout_task, stderr_task])
        except Exception:
            await terminate_process(process, kill=True)
            raise
        finally:
            await asyncio.to_thread(_remove_file, config_path)


def _write_launch_config(options: dict[str, Any]) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
        json.dump(options, fh)
        fh.write("\n")
        return fh.name


def _remove_file(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        import os

        os.remove(path)


__all__ = ["BROWSER_SERVER_LAUNCH_TIMEOUT", "BrowserServerHandle", "BrowserServerLauncher"]
