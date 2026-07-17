from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, model_validator

from . import __version__
from .audio import AudioCapture
from .confirmations import ConfirmationError, ConfirmationExpired
from .data_cleanup import clear_app_owned_screenshots
from .logging_config import clear_rotating_logs
from .models import (
    CommandAccepted,
    CommandRequest,
    ConfirmationDecision,
    EventEnvelope,
    EventType,
    HealthResponse,
    PermissionLevel,
    SettingPatch,
)
from .orchestrator import AssistantBusyError
from .parent_watchdog import ParentProcessWatchdog, cancel_watchdog
from .process_io import run_blocking
from .runtime import AssistantRuntime

logger = logging.getLogger(__name__)


class ToolPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    permission: PermissionLevel | None = None

    @model_validator(mode="after")
    def contains_update(self) -> ToolPolicyPatch:
        if self.enabled is None and self.permission is None:
            raise ValueError("provide enabled or permission")
        return self


class VoiceMuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    muted: bool


class WebSocketAuthentication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["authenticate"]
    token: str


class WebSocketControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cancel", "start_listening", "ping"]


def create_app(
    runtime: AssistantRuntime | None = None,
    *,
    parent_watchdog: ParentProcessWatchdog | None = None,
) -> FastAPI:
    runtime = runtime or AssistantRuntime.create()
    maintenance_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        watchdog_task: asyncio.Task[None] | None = None
        monitor = parent_watchdog
        if monitor is None and runtime.settings.parent_pid is not None:
            monitor = ParentProcessWatchdog(runtime.settings.parent_pid)
        if monitor is not None:

            async def parent_lost() -> None:
                try:
                    await runtime.shutdown()
                except Exception:
                    logger.exception("managed backend could not fully quiesce after parent exit")
                finally:
                    request_exit = getattr(app.state, "request_server_exit", None)
                    if callable(request_exit):
                        request_exit()

            watchdog_task = asyncio.create_task(
                monitor.run(parent_lost), name="managed-parent-watchdog"
            )
        try:
            yield
        finally:
            await cancel_watchdog(watchdog_task)
            await runtime.shutdown()

    app = FastAPI(
        title="JARVIS Assistant Local API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.runtime = runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "http://localhost:1420",
            "http://127.0.0.1:1420",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Assistant-Token"],
    )

    async def authenticate(
        token: Annotated[str | None, Header(alias="X-Assistant-Token")] = None,
    ) -> None:
        expected = runtime.settings.session_token.get_secret_value()
        if token is None or not hmac.compare_digest(token, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid assistant session token",
            )

    auth = Depends(authenticate)

    @app.get("/v1/health", response_model=HealthResponse, dependencies=[auth])
    async def health() -> HealthResponse:
        statuses = await runtime.provider_statuses()
        return HealthResponse(
            status="ok" if all(item.available for item in statuses) else "degraded",
            state=runtime.state.current,
            version=__version__,
            mock_mode=runtime.settings.mock_mode,
        )

    @app.get("/v1/state", dependencies=[auth])
    async def current_state() -> dict[str, str]:
        return {"state": runtime.state.current.value}

    @app.get("/v1/providers/status", dependencies=[auth])
    async def provider_statuses() -> list[dict[str, Any]]:
        statuses = await runtime.provider_statuses()
        return [item.model_dump(mode="json") for item in statuses]

    @app.get("/v1/audio/devices", dependencies=[auth])
    async def audio_devices() -> dict[str, Any]:
        return {
            "devices": await AudioCapture.list_devices(),
            "selected": runtime.settings.microphone_device,
        }

    @app.get("/v1/settings", dependencies=[auth])
    async def get_settings() -> dict[str, Any]:
        return await runtime.orchestrator.settings_snapshot()

    @app.patch("/v1/settings", dependencies=[auth])
    async def update_settings(patch: SettingPatch) -> dict[str, Any]:
        return await runtime.orchestrator.update_settings(patch)

    @app.get("/v1/tools", dependencies=[auth])
    async def tools() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for descriptor in runtime.registry.descriptors():
            if (
                descriptor.permission_category.value == "development"
                and not runtime.settings.developer_mode
            ):
                continue
            enabled, permission = await runtime.permissions.policy_for(descriptor)
            results.append(
                descriptor.model_dump(mode="json")
                | {"enabled": enabled, "permission": permission.value}
            )
        return results

    @app.patch("/v1/tools/{name}", dependencies=[auth])
    async def update_tool(name: str, patch: ToolPolicyPatch) -> dict[str, Any]:
        try:
            descriptor = runtime.registry.get(name).descriptor
        except Exception as exc:
            raise HTTPException(status_code=404, detail="tool not found") from exc
        try:
            enabled, permission = await runtime.permissions.update_policy(
                descriptor, enabled=patch.enabled, permission=patch.permission
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return descriptor.model_dump(mode="json") | {
            "enabled": enabled,
            "permission": permission.value,
        }

    @app.get("/v1/history", dependencies=[auth])
    async def history(limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
        return await runtime.memory.history(limit)

    @app.post("/v1/command", response_model=CommandAccepted, dependencies=[auth])
    async def command(request: CommandRequest) -> CommandAccepted:
        try:
            command_id = await runtime.orchestrator.submit_text(request.text)
        except AssistantBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return CommandAccepted(command_id=command_id)

    @app.post("/v1/listen/start", response_model=CommandAccepted, dependencies=[auth])
    async def start_listening() -> CommandAccepted:
        try:
            command_id = await runtime.orchestrator.start_listening()
        except AssistantBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return CommandAccepted(command_id=command_id)

    @app.post("/v1/listen/stop", dependencies=[auth])
    async def stop_listening() -> dict[str, bool]:
        try:
            await runtime.orchestrator.cancel_and_wait()
        except AssistantBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"cancelled": True}

    @app.post("/v1/cancel", dependencies=[auth])
    async def cancel() -> dict[str, bool]:
        try:
            await runtime.orchestrator.cancel_and_wait()
        except AssistantBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"cancelled": True}

    @app.post("/v1/voice/mute", dependencies=[auth])
    async def mute_voice(request: VoiceMuteRequest) -> dict[str, bool]:
        await runtime.orchestrator.set_voice_muted(request.muted)
        return {"muted": request.muted}

    @app.post("/v1/confirmations/{confirmation_id}/decide", dependencies=[auth])
    async def decide_confirmation(
        confirmation_id: UUID, decision: ConfirmationDecision
    ) -> dict[str, str]:
        try:
            await runtime.confirmations.decide(confirmation_id, decision)
        except ConfirmationExpired as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        except ConfirmationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "recorded", "decision": decision.decision}

    @app.get("/v1/confirmations/pending", dependencies=[auth])
    async def pending_confirmations() -> list[dict[str, object]]:
        return await runtime.confirmations.pending()

    @app.delete("/v1/data", dependencies=[auth])
    async def clear_data() -> dict[str, bool]:
        async with maintenance_lock:
            try:
                await runtime.orchestrator.quiesce()
                await runtime.memory.clear_local_data()
                await runtime.orchestrator.reset_settings()
                await run_blocking(clear_app_owned_screenshots, runtime.settings.data_dir)
                clear_rotating_logs(
                    runtime.settings.log_dir,
                    data_dir=runtime.settings.data_dir,
                    level=runtime.settings.log_level,
                    max_bytes=runtime.settings.log_max_bytes,
                    backup_count=runtime.settings.log_backup_count,
                )
            finally:
                await runtime.orchestrator.resume_operations()
        return {"cleared": True}

    @app.post("/v1/shutdown", dependencies=[auth])
    async def shutdown_backend() -> dict[str, bool]:
        """Quiesce active work before asking the local ASGI server to exit."""
        try:
            await runtime.shutdown()
        except AssistantBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        request_exit = getattr(app.state, "request_server_exit", None)
        if callable(request_exit):
            request_exit()
        return {"shutting_down": True}

    @app.websocket("/v1/events")
    async def event_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            payload = await asyncio.wait_for(websocket.receive_json(), timeout=5)
            authentication = WebSocketAuthentication.model_validate(payload)
        except (TimeoutError, ValueError, json.JSONDecodeError, WebSocketDisconnect):
            await websocket.close(code=4401, reason="authentication required")
            return
        expected = runtime.settings.session_token.get_secret_value()
        if not hmac.compare_digest(authentication.token, expected):
            await websocket.close(code=4401, reason="authentication failed")
            return
        async with runtime.event_bus.subscribe() as queue:
            snapshot = EventEnvelope(
                type=EventType.STATUS_CHANGED,
                payload={"state": runtime.state.current.value, "snapshot": True},
            )
            await websocket.send_json(snapshot.model_dump(mode="json"))
            while True:
                event_task = asyncio.create_task(queue.get())
                receive_task = asyncio.create_task(websocket.receive())
                done, pending = await asyncio.wait(
                    {event_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                try:
                    if event_task in done:
                        event = event_task.result()
                        await websocket.send_json(event.model_dump(mode="json"))
                    if receive_task in done:
                        message = receive_task.result()
                        if message["type"] == "websocket.disconnect":
                            return
                        raw_text = message.get("text")
                        if not raw_text:
                            continue
                        try:
                            control = WebSocketControl.model_validate_json(raw_text)
                        except ValueError:
                            await websocket.close(code=4400, reason="invalid control message")
                            return
                        if control.type == "cancel":
                            await runtime.orchestrator.cancel()
                        elif control.type == "start_listening":
                            try:
                                await runtime.orchestrator.start_listening()
                            except AssistantBusyError:
                                pass
                except (ConnectionError, RuntimeError, WebSocketDisconnect):
                    return

    return app
