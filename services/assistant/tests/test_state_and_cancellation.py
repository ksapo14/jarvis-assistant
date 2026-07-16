from __future__ import annotations

import asyncio

import pytest

from jarvis_assistant.cancellation import CancellationToken, OperationCancelled
from jarvis_assistant.events import EventBus
from jarvis_assistant.models import AssistantState
from jarvis_assistant.state import InvalidStateTransition, StateMachine


async def test_state_machine_emits_valid_transition() -> None:
    bus = EventBus()
    machine = StateMachine(bus)
    async with bus.subscribe() as queue:
        await machine.transition(AssistantState.LISTENING)
        event = await asyncio.wait_for(queue.get(), timeout=1)
    assert machine.current is AssistantState.LISTENING
    assert event.payload["previous"] == "idle"
    assert event.payload["state"] == "listening"


async def test_state_machine_rejects_impossible_transition() -> None:
    machine = StateMachine(EventBus())
    with pytest.raises(InvalidStateTransition):
        await machine.transition(AssistantState.SPEAKING)


async def test_cancellation_interrupts_sleep() -> None:
    token = CancellationToken()
    task = asyncio.create_task(token.sleep(60))
    token.cancel()
    with pytest.raises(OperationCancelled):
        await asyncio.wait_for(task, timeout=1)


def test_cancellation_check_raises() -> None:
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        token.raise_if_cancelled()
