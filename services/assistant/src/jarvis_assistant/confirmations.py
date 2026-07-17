from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from .events import EventBus
from .memory import MemoryService
from .models import ConfirmationDecision, ConfirmationRequest, EventType, RiskLevel, ToolCall


class ConfirmationError(RuntimeError):
    pass


class ConfirmationExpired(ConfirmationError):
    pass


@dataclass(slots=True)
class _PendingConfirmation:
    request: ConfirmationRequest
    token_hash: str
    future: asyncio.Future[Literal["yes", "no"]]


def action_fingerprint(tool_call: ToolCall) -> str:
    canonical = json.dumps(
        {"name": tool_call.name, "arguments": tool_call.arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ConfirmationManager:
    def __init__(
        self,
        memory: MemoryService,
        event_bus: EventBus,
        *,
        timeout_seconds: float = 30,
    ) -> None:
        self._memory = memory
        self._event_bus = event_bus
        self._timeout_seconds = timeout_seconds
        self._pending: dict[UUID, _PendingConfirmation] = {}
        self._lock = asyncio.Lock()

    async def create(
        self, tool_call: ToolCall, risk_level: RiskLevel, prompt: str
    ) -> ConfirmationRequest:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        fingerprint = action_fingerprint(tool_call)
        request = ConfirmationRequest(
            confirmation_token=raw_token,
            tool_call=tool_call,
            risk_level=risk_level,
            prompt=prompt,
            action_fingerprint=fingerprint,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._timeout_seconds),
        )
        future: asyncio.Future[Literal["yes", "no"]] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending[request.id] = _PendingConfirmation(request, token_hash, future)
        await self._memory.record_confirmation(
            confirmation_id=str(request.id),
            tool_name=tool_call.name,
            action_fingerprint=fingerprint,
            token_hash=token_hash,
            prompt=prompt,
            expires_at=request.expires_at.isoformat(),
        )
        await self._event_bus.publish(
            EventType.CONFIRMATION_REQUEST,
            request.model_dump(mode="json", exclude={"confirmation_token"})
            | {"confirmation_token": raw_token},
        )
        return request

    async def decide(self, confirmation_id: UUID, decision: ConfirmationDecision) -> None:
        expired = False
        async with self._lock:
            pending = self._pending.get(confirmation_id)
            if pending is None:
                raise ConfirmationError("confirmation does not exist or was already decided")
            now = datetime.now(UTC)
            if now >= pending.request.expires_at:
                self._pending.pop(confirmation_id, None)
                if not pending.future.done():
                    pending.future.set_result("no")
                expired = True
            if not expired:
                provided_hash = hashlib.sha256(decision.confirmation_token.encode()).hexdigest()
                if not hmac.compare_digest(provided_hash, pending.token_hash):
                    raise ConfirmationError("confirmation token is invalid")
                if not hmac.compare_digest(
                    decision.action_fingerprint, pending.request.action_fingerprint
                ):
                    raise ConfirmationError("the action changed; a fresh confirmation is required")
                if pending.future.done():
                    raise ConfirmationError("confirmation was already decided")
                pending.future.set_result(decision.decision)
        if expired:
            await self._record_resolution(confirmation_id, "expired")
            raise ConfirmationExpired("confirmation expired")
        await self._memory.record_confirmation_decision(str(confirmation_id), decision.decision)
        await self._event_bus.publish(
            EventType.CONFIRMATION_DECISION,
            {"id": str(confirmation_id), "decision": decision.decision},
        )

    async def wait(self, request: ConfirmationRequest) -> Literal["yes", "no"]:
        async with self._lock:
            pending = self._pending.get(request.id)
            if pending is None:
                raise ConfirmationError("confirmation is no longer pending")
            future = pending.future
        timeout = max((request.expires_at - datetime.now(UTC)).total_seconds(), 0)
        try:
            decision = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
            async with self._lock:
                self._pending.pop(request.id, None)
            return decision
        except TimeoutError:
            async with self._lock:
                self._pending.pop(request.id, None)
            await self._record_resolution(request.id, "expired")
            return "no"

    async def cancel_all(self) -> None:
        async with self._lock:
            pending = tuple(self._pending.values())
            self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_result("no")
            await self._record_resolution(item.request.id, "cancelled")

    async def pending(self) -> list[dict[str, object]]:
        async with self._lock:
            pending = tuple(self._pending.values())
        return [
            item.request.model_dump(mode="json", exclude={"confirmation_token"})
            | {"confirmation_token": item.request.confirmation_token}
            for item in pending
        ]

    async def _record_resolution(self, confirmation_id: UUID, decision: str) -> None:
        await self._memory.record_confirmation_decision(str(confirmation_id), decision)
        await self._event_bus.publish(
            EventType.CONFIRMATION_DECISION,
            {"id": str(confirmation_id), "decision": decision},
        )
