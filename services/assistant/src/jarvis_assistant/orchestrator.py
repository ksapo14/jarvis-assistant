from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from .audio import AudioCapture
from .cancellation import CancellationToken, OperationCancelled
from .config import Settings
from .confirmations import ConfirmationError, ConfirmationManager
from .events import EventBus
from .memory import MemoryService
from .models import (
    AssistantState,
    ConfirmationDecision,
    ConfirmationRequest,
    ConversationMessage,
    ConversationRole,
    EventType,
    LanguageModelRequest,
    PermissionLevel,
    SettingPatch,
    ToolCall,
    ToolDescriptor,
    ToolResult,
)
from .permissions import PermissionManager
from .providers.base import (
    LanguageModelProvider,
    ProviderError,
    SpeechToTextProvider,
    TextToSpeechProvider,
    WakeWordProvider,
)
from .providers.piper import PiperTextToSpeechProvider
from .state import StateMachine
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AssistantBusyError(RuntimeError):
    pass


class AssistantOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        event_bus: EventBus,
        state: StateMachine,
        memory: MemoryService,
        permissions: PermissionManager,
        confirmations: ConfirmationManager,
        registry: ToolRegistry,
        speech_to_text: SpeechToTextProvider,
        language_model: LanguageModelProvider,
        text_to_speech: TextToSpeechProvider,
        wake_word: WakeWordProvider,
        audio_capture: AudioCapture,
    ) -> None:
        self.settings = settings
        self.event_bus = event_bus
        self.state = state
        self.memory = memory
        self.permissions = permissions
        self.confirmations = confirmations
        self.registry = registry
        self.speech_to_text = speech_to_text
        self.language_model = language_model
        self.text_to_speech = text_to_speech
        self.wake_word = wake_word
        self.audio_capture = audio_capture
        self.voice_muted = False
        self._operation_lock = asyncio.Lock()
        self._active_task: asyncio.Task[None] | None = None
        self._active_cancellation: CancellationToken | None = None
        self._wake_task: asyncio.Task[None] | None = None
        self._wake_cancellation: CancellationToken | None = None
        self._wake_capture_idle = asyncio.Event()
        self._wake_capture_idle.set()
        self._wake_paused = False
        self._shutdown = asyncio.Event()
        self._persisted_commands: set[UUID] = set()
        self._baseline_settings = self._capture_resettable_settings()

    async def start(self) -> None:
        if self.settings.wake_word_enabled and not self.settings.mock_mode:
            self._wake_task = asyncio.create_task(self._wake_loop(), name="wake-word-loop")

    async def hydrate_persisted_settings(self) -> None:
        persisted = await self.memory.get_settings()
        accepted = set(SettingPatch.model_fields)
        accepted.update({"save_history", "wake_word_sensitivity"})
        filtered = {key: value for key, value in persisted.items() if key in accepted}
        if filtered:
            await self._apply_settings(
                SettingPatch.model_validate(filtered),
                persist=False,
                manage_wake_loop=False,
                emit=False,
            )

    async def _set_wake_word_enabled(self, enabled: bool) -> None:
        """Apply wake-word setting changes immediately without restarting the backend."""
        self.settings.wake_word_enabled = enabled
        if self.settings.mock_mode or self._shutdown.is_set():
            return
        if enabled:
            if self._wake_task is None or self._wake_task.done():
                await self.wake_word.reset()
                self._wake_task = asyncio.create_task(self._wake_loop(), name="wake-word-loop")
            return
        if self._wake_task is not None:
            self._wake_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._wake_task
            self._wake_task = None
        await self.wake_word.reset()

    async def shutdown(self) -> None:
        self._shutdown.set()
        await self.cancel()
        if self._wake_task is not None:
            self._wake_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._wake_task
            self._wake_task = None
        self.permissions.clear_session()

    async def submit_text(self, text: str) -> UUID:
        command_id = uuid4()
        await self._begin_operation(
            command_id,
            text,
            lambda token: self._process_text(command_id, text, token),
        )
        return command_id

    async def start_listening(self, *, wake_activated: bool = False) -> UUID:
        command_id = uuid4()
        await self._begin_operation(
            command_id,
            "[voice command]",
            lambda token: self._capture_and_process(command_id, token),
            before_start=self._announce_wake_activation if wake_activated else None,
        )
        return command_id

    async def _announce_wake_activation(self) -> None:
        if self.state.current is not AssistantState.IDLE:
            raise AssistantBusyError("another operation started before wake activation completed")
        await self.state.transition(AssistantState.WAKE_WORD_DETECTED)
        await self.event_bus.publish(
            EventType.STATUS_CHANGED,
            {
                "state": AssistantState.WAKE_WORD_DETECTED.value,
                "activation_sound": True,
            },
        )
        if self.settings.play_activation_sound:
            try:
                await asyncio.to_thread(_play_activation_sound)
            except Exception as exc:
                logger.warning("activation sound could not play", extra={"error": str(exc)})

    async def _begin_operation(
        self,
        command_id: UUID,
        request_label: str,
        operation: Any,
        before_start: Any | None = None,
    ) -> None:
        async with self._operation_lock:
            if self._active_task is not None and not self._active_task.done():
                raise AssistantBusyError("the assistant is already handling a command")
            self._wake_paused = True
            try:
                await self._stop_wake_capture()
                if before_start is not None:
                    await before_start()
                token = CancellationToken()
                self._active_cancellation = token
                if self.settings.save_conversation_history:
                    await self.memory.start_command(str(command_id), request_label)
                    self._persisted_commands.add(command_id)
                task = asyncio.create_task(
                    self._run_operation(command_id, operation, token),
                    name=f"assistant-command-{command_id}",
                )
                self._active_task = task
            except BaseException:
                self._active_cancellation = None
                self._persisted_commands.discard(command_id)
                self._wake_paused = False
                await self._return_to_idle()
                raise

    async def _stop_wake_capture(self) -> None:
        cancellation = self._wake_cancellation
        if cancellation is None:
            return
        cancellation.cancel()
        try:
            await asyncio.wait_for(self._wake_capture_idle.wait(), timeout=3)
        except TimeoutError as exc:
            raise AssistantBusyError("the wake-word microphone stream did not stop safely") from exc

    async def _run_operation(
        self, command_id: UUID, operation: Any, cancellation: CancellationToken
    ) -> None:
        try:
            response = await operation(cancellation)
            if command_id in self._persisted_commands:
                await self.memory.finish_command(str(command_id), response or "", "completed")
        except OperationCancelled:
            logger.info("assistant operation cancelled", extra={"command_id": str(command_id)})
            if command_id in self._persisted_commands:
                await self.memory.finish_command(str(command_id), "Cancelled", "cancelled")
            await self.event_bus.publish(EventType.CANCELLATION, {"command_id": str(command_id)})
            await self._return_to_idle()
        except Exception as exc:
            logger.exception("assistant operation failed", extra={"command_id": str(command_id)})
            if command_id in self._persisted_commands:
                await self.memory.finish_command(str(command_id), str(exc), "failed")
            if self.state.current is not AssistantState.ERROR:
                with suppress(Exception):
                    await self.state.transition(AssistantState.ERROR, str(exc))
            await self.event_bus.publish(
                EventType.ERROR,
                {
                    "command_id": str(command_id),
                    "code": getattr(exc, "code", "assistant_error"),
                    "message": _plain_error(exc),
                },
            )
            await self._return_to_idle()
        finally:
            async with self._operation_lock:
                if asyncio.current_task() is self._active_task:
                    self._active_task = None
                    self._active_cancellation = None
                    self._wake_paused = False
                self._persisted_commands.discard(command_id)

    async def _capture_and_process(self, command_id: UUID, cancellation: CancellationToken) -> str:
        if self.state.current is AssistantState.IDLE:
            await self.state.transition(AssistantState.LISTENING)
        elif self.state.current is AssistantState.WAKE_WORD_DETECTED:
            await self.state.transition(AssistantState.LISTENING)
        await self.state.transition(AssistantState.TRANSCRIBING)
        final_segments: list[str] = []
        audio = (
            self._mock_audio()
            if self.settings.mock_mode
            else self.audio_capture.frames(cancellation)
        )
        try:
            async with asyncio.timeout(self.settings.speech_timeout_seconds):
                async for transcript in self.speech_to_text.transcribe(audio, cancellation):
                    if transcript.is_final and transcript.text.strip():
                        segment = transcript.text.strip()
                        if not final_segments or final_segments[-1] != segment:
                            final_segments.append(segment)
                        cumulative = " ".join(final_segments)
                        await self.event_bus.publish(
                            EventType.FINAL_TRANSCRIPT,
                            transcript.model_copy(update={"text": cumulative}).model_dump(
                                mode="json"
                            ),
                        )
                    elif transcript.text:
                        await self.event_bus.publish(
                            EventType.PARTIAL_TRANSCRIPT, transcript.model_dump(mode="json")
                        )
                    if transcript.speech_final:
                        break
        except TimeoutError as exc:
            raise ProviderError("speech capture timed out") from exc
        final_text = " ".join(final_segments)
        if not final_text:
            raise ProviderError("no speech was detected before the timeout")
        if command_id in self._persisted_commands:
            await self.memory.update_command_request(str(command_id), final_text)
        return await self._process_text(command_id, final_text, cancellation)

    async def _process_text(
        self, command_id: UUID, text: str, cancellation: CancellationToken
    ) -> str:
        cancellation.raise_if_cancelled()
        if self.state.current in {AssistantState.IDLE, AssistantState.TRANSCRIBING}:
            await self.state.transition(AssistantState.THINKING)
        user_message = ConversationMessage(role=ConversationRole.USER, content=text)
        save_history = command_id in self._persisted_commands
        if save_history:
            await self.memory.add_conversation(user_message)
            history = [
                message
                for message in await self.memory.recent_conversation(
                    self.settings.max_history_messages
                )
                if message.role is not ConversationRole.TOOL
            ]
            summary = await self.memory.latest_summary()
        else:
            history = [user_message]
            summary = None
        descriptors = await self._enabled_descriptors()
        messages = history
        for _iteration in range(4):
            cancellation.raise_if_cancelled()
            response = await self.language_model.complete(
                LanguageModelRequest(
                    messages=messages,
                    tools=descriptors,
                    long_term_context=summary,
                ),
                cancellation,
            )
            cancellation.raise_if_cancelled()
            if response.tool_calls:
                messages.append(
                    ConversationMessage(
                        role=ConversationRole.ASSISTANT,
                        content=response.text,
                        tool_calls=response.tool_calls,
                    )
                )
                for call in response.tool_calls:
                    result = await self._execute_tool(command_id, call, cancellation)
                    full_tool_message = ConversationMessage(
                        role=ConversationRole.TOOL,
                        name=call.name,
                        tool_call_id=call.id,
                        content=json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                    )
                    messages.append(full_tool_message)
                    stored_content = (
                        result.summary
                        if call.name
                        in {"read_clipboard", "set_clipboard", "type_text", "write_text_file"}
                        else full_tool_message.content
                    )
                    if save_history:
                        await self.memory.add_conversation(
                            full_tool_message.model_copy(update={"content": stored_content})
                        )
                if self.state.current is not AssistantState.THINKING:
                    await self.state.transition(AssistantState.THINKING)
                continue
            final_text = response.text.strip() or response.spoken_text.strip()
            spoken_text = response.spoken_text.strip() or final_text
            if not final_text:
                final_text = "I couldn't produce a reliable response."
                spoken_text = final_text
            assistant_message = ConversationMessage(
                role=ConversationRole.ASSISTANT, content=final_text
            )
            if save_history:
                await self.memory.add_conversation(assistant_message)
                await self._update_local_summary()
            await self.event_bus.publish(
                EventType.ASSISTANT_RESPONSE,
                {
                    "command_id": str(command_id),
                    "text": final_text,
                    "spoken_text": spoken_text,
                },
            )
            if spoken_text and not self.voice_muted:
                await self.state.transition(AssistantState.SPEAKING)
                try:
                    await self.text_to_speech.speak(spoken_text, cancellation)
                except ProviderError as exc:
                    logger.warning("text-to-speech unavailable", extra={"error": str(exc)})
                    await self.event_bus.publish(
                        EventType.ERROR,
                        {"code": exc.code, "message": _plain_error(exc), "recoverable": True},
                    )
            await self._return_to_idle()
            return final_text
        raise ProviderError("the model exceeded the maximum tool-call rounds")

    async def _execute_tool(
        self, command_id: UUID, call: ToolCall, cancellation: CancellationToken
    ) -> ToolResult:
        cancellation.raise_if_cancelled()
        tool, arguments = self.registry.validate(call)
        descriptor = tool.descriptor
        authorization = await self.permissions.authorize(descriptor)
        if authorization.allowed:
            call, arguments = await tool.bind_confirmation(call, arguments, cancellation)
        cancellation.raise_if_cancelled()
        await self.event_bus.publish(
            EventType.TOOL_PROPOSAL,
            {
                "command_id": str(command_id),
                "tool_call": call.model_dump(mode="json"),
                "risk_level": descriptor.risk_level.value,
            },
        )
        confirmation_result: str | None = None
        confirmed = authorization.allowed and not authorization.requires_confirmation
        if not authorization.allowed:
            result = ToolResult(
                tool_call_id=call.id,
                tool_name=call.name,
                success=False,
                summary=f"Permission denied: {authorization.reason}.",
                error_code="permission_denied",
            )
        else:
            if authorization.requires_confirmation:
                await self.state.transition(AssistantState.WAITING_FOR_CONFIRMATION)
                cancellation.raise_if_cancelled()
                confirmation = await self.confirmations.create(
                    call, descriptor.risk_level, tool.preview(arguments)
                )
                if not self.voice_muted:
                    try:
                        await self.text_to_speech.speak(
                            _spoken_confirmation_prompt(confirmation.prompt), cancellation
                        )
                    except ProviderError as exc:
                        logger.warning(
                            "could not speak confirmation prompt",
                            extra={"error": str(exc), "tool": call.name},
                        )
                        await self.event_bus.publish(
                            EventType.ERROR,
                            {
                                "code": exc.code,
                                "message": _plain_error(exc),
                                "recoverable": True,
                            },
                        )
                confirmation_result = await self._wait_for_confirmation(confirmation, cancellation)
                cancellation.raise_if_cancelled()
                if confirmation_result != "yes":
                    await self.state.transition(AssistantState.THINKING)
                    result = ToolResult(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        success=False,
                        summary="The user declined or did not confirm the action.",
                        error_code="confirmation_declined",
                    )
                    await self._record_tool(
                        command_id, call, result, descriptor, confirmation_result
                    )
                    return result
                fresh_tool, fresh_arguments = self.registry.validate(call)
                fresh_descriptor = fresh_tool.descriptor
                fresh_authorization = await self.permissions.authorize(fresh_descriptor)
                if (
                    not fresh_authorization.allowed
                    or fresh_descriptor.risk_level is not descriptor.risk_level
                    or fresh_descriptor.permission_category is not descriptor.permission_category
                    or fresh_descriptor.confirmation_required != descriptor.confirmation_required
                ):
                    await self.state.transition(AssistantState.THINKING)
                    result = ToolResult(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        success=False,
                        summary="Permission or tool policy changed after confirmation.",
                        error_code="permission_changed",
                    )
                    await self._record_tool(command_id, call, result, descriptor, "denied")
                    return result
                tool, arguments = fresh_tool, fresh_arguments
                confirmed = True
                enabled, permission = await self.permissions.policy_for(descriptor)
                if enabled and permission is PermissionLevel.ALLOW_SESSION:
                    self.permissions.grant_for_session(descriptor.name)
            cancellation.raise_if_cancelled()
            await self.state.transition(AssistantState.EXECUTING)
            result = await self.registry.execute(call, cancellation, confirmed=confirmed)
            await self.state.transition(AssistantState.THINKING)
        await self._record_tool(command_id, call, result, descriptor, confirmation_result)
        return result

    async def _wait_for_confirmation(
        self,
        confirmation: ConfirmationRequest,
        cancellation: CancellationToken,
    ) -> str:
        wait_task = asyncio.create_task(self.confirmations.wait(confirmation))
        if self.settings.mock_mode:
            return await wait_task
        speech_cancellation = CancellationToken()
        speech_task = asyncio.create_task(self._listen_for_spoken_confirmation(speech_cancellation))
        try:
            done, _pending = await asyncio.wait(
                {wait_task, speech_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if wait_task in done:
                return wait_task.result()
            decision = speech_task.result()
            if decision is not None:
                with suppress(ConfirmationError):
                    await self.confirmations.decide(
                        confirmation.id,
                        ConfirmationDecision(
                            decision=decision,
                            confirmation_token=confirmation.confirmation_token,
                            action_fingerprint=confirmation.action_fingerprint,
                        ),
                    )
            return await wait_task
        finally:
            speech_cancellation.cancel()
            if not speech_task.done():
                speech_task.cancel()
            with suppress(asyncio.CancelledError):
                await speech_task
            cancellation.raise_if_cancelled()

    async def _listen_for_spoken_confirmation(self, cancellation: CancellationToken) -> str | None:
        segments: list[str] = []
        try:
            async with asyncio.timeout(8):
                async for transcript in self.speech_to_text.transcribe(
                    self.audio_capture.frames(cancellation), cancellation
                ):
                    if transcript.text:
                        event_type = (
                            EventType.FINAL_TRANSCRIPT
                            if transcript.is_final
                            else EventType.PARTIAL_TRANSCRIPT
                        )
                        await self.event_bus.publish(event_type, transcript.model_dump(mode="json"))
                    if transcript.is_final and transcript.text.strip():
                        segment = transcript.text.strip()
                        if not segments or segments[-1] != segment:
                            segments.append(segment)
                    if transcript.speech_final:
                        break
        except (TimeoutError, ProviderError):
            return None
        return _parse_confirmation_speech(" ".join(segments))

    async def _record_tool(
        self,
        command_id: UUID,
        call: ToolCall,
        result: ToolResult,
        descriptor: ToolDescriptor,
        confirmation_result: str | None,
    ) -> None:
        logger.info(
            "tool execution completed",
            extra={
                "command_id": str(command_id),
                "tool": call.name,
                "success": result.success,
                "risk": descriptor.risk_level.value,
            },
        )
        await self.memory.add_tool_history(
            command_id=str(command_id),
            tool_name=call.name,
            arguments=call.arguments,
            result=result,
            risk_level=descriptor.risk_level,
            confirmation_result=confirmation_result,
        )
        await self.event_bus.publish(
            EventType.TOOL_EXECUTION_RESULT,
            {"command_id": str(command_id), **result.model_dump(mode="json")},
        )

    async def _update_local_summary(self) -> None:
        messages = await self.memory.recent_conversation(12)
        lines: list[str] = []
        labels = {
            ConversationRole.USER: "User",
            ConversationRole.ASSISTANT: "Assistant",
        }
        for message in messages:
            label = labels.get(message.role)
            if label is None:
                continue
            content = " ".join(message.content.split())[:240]
            if content:
                lines.append(f"{label}: {content}")
        summary = "\n".join(lines)[-2_000:]
        if summary:
            await self.memory.set_summary(summary)

    async def _enabled_descriptors(self) -> list[ToolDescriptor]:
        descriptors: list[ToolDescriptor] = []
        for descriptor in self.registry.descriptors():
            if (
                descriptor.permission_category.value == "development"
                and not self.settings.developer_mode
            ):
                continue
            enabled, permission = await self.permissions.policy_for(descriptor)
            if enabled and permission is not PermissionLevel.DISABLED:
                descriptors.append(descriptor.model_copy(update={"enabled": True}))
        return descriptors

    async def cancel(self) -> None:
        cancellation = self._active_cancellation
        if cancellation is not None:
            cancellation.cancel()
        await self.text_to_speech.cancel()
        await self.confirmations.cancel_all()
        active_task = self._active_task
        if active_task is None or active_task is asyncio.current_task():
            return
        try:
            await asyncio.wait_for(asyncio.shield(active_task), timeout=10)
        except TimeoutError as exc:
            raise AssistantBusyError(
                "the active operation did not reach a safe stopping point; data was not cleared"
            ) from exc

    async def set_voice_muted(self, muted: bool) -> None:
        self.voice_muted = muted
        if muted:
            await self.text_to_speech.cancel()
        await self.memory.set_setting("voice_muted", muted)
        await self.event_bus.publish(EventType.SETTINGS_UPDATED, {"voice_muted": muted})

    async def update_settings(self, patch: SettingPatch) -> dict[str, Any]:
        await self._apply_settings(patch, persist=True, manage_wake_loop=True, emit=True)
        return await self.settings_snapshot()

    async def reset_settings(self) -> dict[str, Any]:
        reset_values = dict(self._baseline_settings)
        reset_values["launch_on_startup"] = False
        await self._apply_settings(
            SettingPatch.model_validate(reset_values),
            persist=False,
            manage_wake_loop=True,
            emit=True,
        )
        self.permissions.clear_session()
        return await self.settings_snapshot()

    def _capture_resettable_settings(self) -> dict[str, Any]:
        return {
            "launch_on_startup": self.settings.launch_on_startup,
            "minimize_to_tray": self.settings.minimize_to_tray,
            "play_activation_sound": self.settings.play_activation_sound,
            "save_conversation_history": self.settings.save_conversation_history,
            "developer_mode": self.settings.developer_mode,
            "wake_word_enabled": self.settings.wake_word_enabled,
            "wake_phrase": self.settings.wake_word_phrase,
            "wake_sensitivity": self.settings.wake_word_sensitivity,
            "microphone_device": self.settings.microphone_device,
            "push_to_talk_shortcut": self.settings.push_to_talk_shortcut,
            "global_shortcut": self.settings.global_shortcut,
            "piper_executable_path": (
                str(self.settings.piper_executable_path)
                if self.settings.piper_executable_path
                else None
            ),
            "piper_model_path": (
                str(self.settings.piper_model_path) if self.settings.piper_model_path else None
            ),
            "speech_rate": self.settings.speech_rate,
            "speech_volume": self.settings.speech_volume,
            "voice_muted": False,
            "preferred_applications": dict(self.settings.preferred_applications),
        }

    async def _apply_settings(
        self,
        patch: SettingPatch,
        *,
        persist: bool,
        manage_wake_loop: bool,
        emit: bool,
    ) -> None:
        updates = patch.model_dump(exclude_unset=True)
        settings_attribute_names = {
            "wake_phrase": "wake_word_phrase",
            "wake_sensitivity": "wake_word_sensitivity",
        }
        for key, value in updates.items():
            if persist:
                await self.memory.set_setting(key, value)
            attribute_name = settings_attribute_names.get(key, key)
            if hasattr(self.settings, attribute_name):
                if key in {"piper_executable_path", "piper_model_path"}:
                    value = _expand_optional_path(value)
                setattr(self.settings, attribute_name, value)
        if "microphone_device" in updates:
            self.audio_capture.configure(device=updates["microphone_device"])
        if "wake_phrase" in updates or "wake_sensitivity" in updates:
            await self.wake_word.configure(
                phrase=self.settings.wake_word_phrase,
                sensitivity=self.settings.wake_word_sensitivity,
            )
        if "voice_muted" in updates:
            self.voice_muted = bool(updates["voice_muted"])
            if self.voice_muted:
                await self.text_to_speech.cancel()
        if manage_wake_loop and "wake_word_enabled" in updates:
            await self._set_wake_word_enabled(bool(updates["wake_word_enabled"]))
        if manage_wake_loop and "microphone_device" in updates and self.settings.wake_word_enabled:
            await self._set_wake_word_enabled(False)
            await self._set_wake_word_enabled(True)
        if isinstance(self.text_to_speech, PiperTextToSpeechProvider):
            self.text_to_speech.update(
                speech_rate=updates.get("speech_rate"),
                volume=updates.get("speech_volume"),
                executable_path=self.settings.piper_executable_path,
                model_path=self.settings.piper_model_path,
                update_executable_path="piper_executable_path" in updates,
                update_model_path="piper_model_path" in updates,
            )
        if emit:
            await self.event_bus.publish(EventType.SETTINGS_UPDATED, updates)

    async def settings_snapshot(self) -> dict[str, Any]:
        persisted = await self.memory.get_settings()
        defaults = {
            "launch_on_startup": self.settings.launch_on_startup,
            "minimize_to_tray": self.settings.minimize_to_tray,
            "play_activation_sound": self.settings.play_activation_sound,
            "save_conversation_history": self.settings.save_conversation_history,
            "developer_mode": self.settings.developer_mode,
            "wake_word_enabled": self.settings.wake_word_enabled,
            "wake_phrase": self.settings.wake_word_phrase,
            "wake_sensitivity": self.settings.wake_word_sensitivity,
            "microphone_device": self.settings.microphone_device,
            "push_to_talk_shortcut": self.settings.push_to_talk_shortcut,
            "global_shortcut": self.settings.global_shortcut,
            "piper_executable_path": str(self.settings.piper_executable_path or ""),
            "piper_model_path": str(self.settings.piper_model_path or ""),
            "speech_rate": self.settings.speech_rate,
            "speech_volume": self.settings.speech_volume,
            "voice_muted": self.voice_muted,
            "preferred_applications": dict(self.settings.preferred_applications),
        }
        snapshot = defaults | persisted
        for key in ("piper_executable_path", "piper_model_path"):
            if snapshot.get(key) is None:
                snapshot[key] = ""
        tool_permissions: dict[str, str] = {}
        for descriptor in self.registry.descriptors():
            if (
                descriptor.permission_category.value == "development"
                and not self.settings.developer_mode
            ):
                continue
            enabled, permission = await self.permissions.policy_for(descriptor)
            tool_permissions[descriptor.name] = (
                permission.value if enabled else PermissionLevel.DISABLED.value
            )
        snapshot["tool_permissions"] = tool_permissions
        return snapshot

    async def _return_to_idle(self) -> None:
        if self.state.current is AssistantState.IDLE:
            return
        if self.state.current is AssistantState.ERROR:
            await self.state.transition(AssistantState.IDLE)
            return
        try:
            await self.state.transition(AssistantState.IDLE)
        except Exception:
            await self.state.recover_to_idle()

    async def _wake_loop(self) -> None:
        last_activation = 0.0
        while not self._shutdown.is_set():
            if self._wake_paused or self.state.current is not AssistantState.IDLE:
                await asyncio.sleep(0.2)
                continue
            token = CancellationToken()
            if self._wake_paused:
                continue
            self._wake_cancellation = token
            self._wake_capture_idle.clear()
            activated = False
            failed = False
            frames = self.audio_capture.frames(token)
            try:
                async for frame in frames:
                    if self._shutdown.is_set() or self.state.current is not AssistantState.IDLE:
                        token.cancel()
                        break
                    detected = await self.wake_word.detect(frame)
                    if (
                        self._wake_paused
                        or token.cancelled
                        or self.state.current is not AssistantState.IDLE
                    ):
                        token.cancel()
                        break
                    if detected:
                        current = time.monotonic()
                        if current - last_activation < self.settings.wake_word_cooldown_seconds:
                            continue
                        last_activation = current
                        activated = True
                        token.cancel()
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed = True
                logger.warning("wake-word loop paused after an error", extra={"error": str(exc)})
                await self.event_bus.publish(
                    EventType.ERROR,
                    {"code": getattr(exc, "code", "wake_word_error"), "message": _plain_error(exc)},
                )
            finally:
                token.cancel()
                await frames.aclose()
                if self._wake_cancellation is token:
                    self._wake_cancellation = None
                self._wake_capture_idle.set()
            if failed:
                await asyncio.sleep(5)
                continue
            if activated and not self._shutdown.is_set() and not self._wake_paused:
                try:
                    await self.start_listening(wake_activated=True)
                except AssistantBusyError:
                    logger.info("wake activation yielded to a manual command")

    @staticmethod
    async def _mock_audio() -> AsyncIterator[bytes]:
        yield b"\x00\x00" * 1_280


def _plain_error(error: Exception) -> str:
    message = str(error).strip()
    if not message:
        return "The operation failed unexpectedly."
    return message[:500]


def _spoken_confirmation_prompt(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    if len(normalized) <= 600:
        return normalized
    return normalized[:560].rsplit(" ", 1)[0] + ". The complete action is shown on screen."


def _expand_optional_path(value: Any) -> Path | None:
    return Path(value).expanduser() if value else None


def _parse_confirmation_speech(text: str) -> Literal["yes", "no"] | None:
    normalized = " ".join(token.strip(".,!?;:\"'").casefold() for token in text.split())
    if normalized in {"yes", "yes please", "yes continue", "confirm"}:
        return "yes"
    if normalized in {"no", "no thanks", "do not", "cancel"}:
        return "no"
    return None


def _play_activation_sound() -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_OK)
    except (ImportError, RuntimeError):
        logger.debug("activation sound was unavailable", exc_info=True)
