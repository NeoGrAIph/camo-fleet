"""Subprocess management helpers for launching Playwright browser servers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
from asyncio import subprocess as aio_subprocess
from typing import Any

from camoufox import launch_options
from playwright._impl._driver import compute_driver_executable

LOGGER = logging.getLogger(__name__)

BROWSER_SERVER_LAUNCH_TIMEOUT = 45


class SubprocessBrowserServer:
    """Wrap a spawned Playwright browser server process."""

    def __init__(
        self,
        process: aio_subprocess.Process,
        ws_endpoint: str,
        drain_tasks: list[asyncio.Task[None]],
    ) -> None:
        self._process = process
        self.ws_endpoint = ws_endpoint
        self._drain_tasks = drain_tasks

    async def close(self) -> None:
        """Terminate the subprocess and cancel background drain tasks."""

        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()

        for task in self._drain_tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class BrowserLauncher:
    """Create Camoufox-driven Playwright browser server subprocesses."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or LOGGER

    async def launch(
        self,
        *,
        headless: bool,
        vnc: bool,
        display: str | None,
        override_proxy: dict[str, Any] | None = None,
    ) -> SubprocessBrowserServer:
        """Launch a new browser server configured through Camoufox."""

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
                    process.stdout.readline(), timeout=BROWSER_SERVER_LAUNCH_TIMEOUT
                )
            except TimeoutError as exc:
                await _terminate_process(process)
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
                _drain_stream(process.stdout, "camoufox-stdout", self._logger),
                name="camoufox-server-stdout",
            )
            stderr_task = asyncio.create_task(
                _drain_stream(process.stderr, "camoufox-stderr", self._logger),
                name="camoufox-server-stderr",
            )
            return SubprocessBrowserServer(process, ws_endpoint, [stdout_task, stderr_task])
        except Exception:
            await _terminate_process(process, kill=True)
            raise
        finally:
            await asyncio.to_thread(_remove_file, config_path)


async def _drain_stream(
    stream: asyncio.StreamReader | None, prefix: str, logger: logging.Logger
) -> None:
    """Drain subprocess output to avoid blocking pipes and log diagnostic lines."""

    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            break
        logger.debug("%s: %s", prefix, line.decode().rstrip())


def _write_launch_config(options: dict[str, Any]) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
        json.dump(options, fh)
        fh.write("\n")
        return fh.name


def _remove_file(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        import os

        os.remove(path)


async def _terminate_process(process: aio_subprocess.Process, *, kill: bool = False) -> None:
    if process.returncode is not None:
        return
    if not kill:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
            return
        except TimeoutError:
            LOGGER.warning("Camoufox server did not exit after terminate; killing")
    process.kill()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


__all__ = ["BrowserLauncher", "SubprocessBrowserServer", "BROWSER_SERVER_LAUNCH_TIMEOUT"]
