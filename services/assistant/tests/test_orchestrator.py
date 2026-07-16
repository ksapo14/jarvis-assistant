from __future__ import annotations

import asyncio

from jarvis_assistant.config import Settings
from jarvis_assistant.models import AssistantState
from jarvis_assistant.providers.mock import MockTextToSpeechProvider
from jarvis_assistant.runtime import AssistantRuntime


async def test_mock_end_to_end_executes_safe_tool_and_speaks(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    try:
        await runtime.orchestrator.submit_text("What time is it?")
        task = runtime.orchestrator._active_task
        assert task is not None
        await asyncio.wait_for(task, timeout=3)
        history = await runtime.memory.history()
        assert history[0]["tool_name"] == "get_current_datetime"
        assert history[0]["tool_result"]["success"] is True
        assert runtime.state.current is AssistantState.IDLE
        tts = runtime.orchestrator.text_to_speech
        assert isinstance(tts, MockTextToSpeechProvider)
        assert tts.spoken == ["Done."]
    finally:
        await runtime.shutdown()


async def test_mock_push_to_talk_pipeline_needs_no_microphone(settings: Settings) -> None:
    runtime = AssistantRuntime.create(settings)
    await runtime.start()
    try:
        await runtime.orchestrator.start_listening()
        task = runtime.orchestrator._active_task
        assert task is not None
        await asyncio.wait_for(task, timeout=3)
        assert runtime.state.current is AssistantState.IDLE
    finally:
        await runtime.shutdown()
