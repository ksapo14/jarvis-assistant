from __future__ import annotations

import asyncio

from .events import EventBus
from .models import AssistantState, EventType


class InvalidStateTransition(RuntimeError):
    pass


_ALLOWED: dict[AssistantState, frozenset[AssistantState]] = {
    AssistantState.IDLE: frozenset(
        {
            AssistantState.WAKE_WORD_DETECTED,
            AssistantState.LISTENING,
            AssistantState.THINKING,
            AssistantState.ERROR,
        }
    ),
    AssistantState.WAKE_WORD_DETECTED: frozenset(
        {AssistantState.LISTENING, AssistantState.IDLE, AssistantState.ERROR}
    ),
    AssistantState.LISTENING: frozenset(
        {AssistantState.TRANSCRIBING, AssistantState.IDLE, AssistantState.ERROR}
    ),
    AssistantState.TRANSCRIBING: frozenset(
        {AssistantState.THINKING, AssistantState.IDLE, AssistantState.ERROR}
    ),
    AssistantState.THINKING: frozenset(
        {
            AssistantState.WAITING_FOR_CONFIRMATION,
            AssistantState.EXECUTING,
            AssistantState.SPEAKING,
            AssistantState.IDLE,
            AssistantState.ERROR,
        }
    ),
    AssistantState.WAITING_FOR_CONFIRMATION: frozenset(
        {
            AssistantState.EXECUTING,
            AssistantState.THINKING,
            AssistantState.IDLE,
            AssistantState.ERROR,
        }
    ),
    AssistantState.EXECUTING: frozenset(
        {
            AssistantState.THINKING,
            AssistantState.SPEAKING,
            AssistantState.IDLE,
            AssistantState.ERROR,
        }
    ),
    AssistantState.SPEAKING: frozenset({AssistantState.IDLE, AssistantState.ERROR}),
    AssistantState.ERROR: frozenset({AssistantState.IDLE}),
}


class StateMachine:
    def __init__(self, event_bus: EventBus, initial: AssistantState = AssistantState.IDLE) -> None:
        self._event_bus = event_bus
        self._state = initial
        self._lock = asyncio.Lock()

    @property
    def current(self) -> AssistantState:
        return self._state

    async def transition(self, target: AssistantState, detail: str | None = None) -> None:
        async with self._lock:
            source = self._state
            if source == target:
                return
            if target not in _ALLOWED[source]:
                raise InvalidStateTransition(f"invalid state transition: {source} -> {target}")
            self._state = target
        await self._event_bus.publish(
            EventType.STATUS_CHANGED,
            {"previous": source.value, "state": target.value, "detail": detail},
        )

    async def recover_to_idle(self) -> None:
        if self.current is AssistantState.IDLE:
            return
        if self.current is not AssistantState.ERROR:
            await self.transition(AssistantState.ERROR)
        await self.transition(AssistantState.IDLE)
