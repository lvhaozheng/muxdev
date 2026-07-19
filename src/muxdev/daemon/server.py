"""Executable daemon server entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import os

import uvicorn

from ..api.app import create_app
from .paths import DEFAULT_API_PORT, DEFAULT_HOST, DEFAULT_UI_PORT, default_daemon_paths


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the muxdev daemon")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--lease-seconds", type=float)
    parser.add_argument("--heartbeat-seconds", type=float)
    args = parser.parse_args(argv)

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("v0.2 local daemon refuses non-loopback binding")

    paths = default_daemon_paths().ensure()
    paths.pid_path.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: paths.pid_path.unlink(missing_ok=True))
    from .tasks import TaskManager

    manager = TaskManager(
        paths=paths,
        worker_count=max(1, args.workers) if args.workers is not None else None,
        lease_ms=max(1_000, int(args.lease_seconds * 1_000)) if args.lease_seconds is not None else None,
        heartbeat_ms=max(100, int(args.heartbeat_seconds * 1_000)) if args.heartbeat_seconds is not None else None,
    )
    app = create_app(task_manager=manager, enforce_auth=True)
    asyncio.run(_serve(app, host=args.host, api_port=args.api_port, ui_port=args.ui_port))


async def _serve(app: object, *, host: str, api_port: int, ui_port: int) -> None:
    if api_port == ui_port:
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=api_port, log_level="info"))
        await server.serve()
        return
    api_server = uvicorn.Server(uvicorn.Config(app, host=host, port=api_port, log_level="info"))
    ui_server = uvicorn.Server(uvicorn.Config(app, host=host, port=ui_port, log_level="info"))
    await asyncio.gather(api_server.serve(), ui_server.serve())


if __name__ == "__main__":
    main()
