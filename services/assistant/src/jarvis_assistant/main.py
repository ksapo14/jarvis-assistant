from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
from pathlib import Path

import uvicorn

from .api import create_app
from .config import Settings
from .logging_config import configure_logging
from .runtime import AssistantRuntime
from .single_instance import AlreadyRunningError, SingleInstanceLock

logger = logging.getLogger(__name__)


def _bind_listener(host: str, port: int) -> socket.socket:
    """Bind the loopback listener in this process so port zero is race-free."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.set_inheritable(False)
        listener.bind((host, port))
        listener.listen(2048)
    except BaseException:
        listener.close()
        raise
    return listener


def _write_readiness(path: Path, nonce: str, port: int) -> None:
    """Atomically publish the one-time managed-backend readiness response."""
    payload = {
        "nonce": nonce,
        "port": port,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def _serve(
    server: uvicorn.Server,
    listener: socket.socket,
    settings: Settings,
) -> None:
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        if settings.readiness_file is not None:
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError("assistant backend exited before becoming ready")
                await asyncio.sleep(0.01)
            assert settings.readiness_nonce is not None
            actual_port = int(listener.getsockname()[1])
            try:
                await asyncio.to_thread(
                    _write_readiness,
                    settings.readiness_file,
                    settings.readiness_nonce.get_secret_value(),
                    actual_port,
                )
            except BaseException:
                server.should_exit = True
                await server_task
                raise
        await server_task
    finally:
        if not server_task.done():
            server.should_exit = True
            await server_task


def main() -> None:
    settings = Settings()
    configure_logging(
        settings.log_dir,
        settings.log_level,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    lock = SingleInstanceLock(settings.data_dir / "assistant.lock")
    try:
        lock.acquire()
    except AlreadyRunningError:
        logger.error("assistant backend is already running")
        raise SystemExit(2) from None
    try:
        runtime = AssistantRuntime.create(settings)
        app = create_app(runtime)
        listener = _bind_listener(settings.host, settings.port)
        actual_port = int(listener.getsockname()[1])
        logger.info(
            "assistant backend starting",
            extra={"host": settings.host, "port": actual_port, "mock_mode": settings.mock_mode},
        )
        try:
            config = uvicorn.Config(
                app=app,
                host=settings.host,
                port=settings.port,
                log_config=None,
                access_log=False,
                timeout_graceful_shutdown=3,
            )
            server = uvicorn.Server(config)
            app.state.request_server_exit = lambda: setattr(server, "should_exit", True)
            asyncio.run(_serve(server, listener, settings))
        finally:
            listener.close()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
