import { describe, expect, it } from "vitest";
import {
  normalizeActivity,
  normalizeConfirmation,
  normalizeEvent,
  normalizeProvider,
  normalizeSettings,
  normalizeSnapshot,
  normalizeTool,
} from "@/api/normalizers";

describe("API normalizers", () => {
  it("normalizes snake_case snapshots without losing boolean false values", () => {
    expect(
      normalizeSnapshot({
        state: "transcribing",
        detail: "Finalizing audio",
        live_transcript: "partial words",
        final_transcript: "",
        recent_action: "Captured audio",
        microphone_active: false,
        wake_word_paused: true,
        voice_muted: false,
        connected: false,
        updated_at: "2026-07-16T12:00:00.000Z",
      }),
    ).toMatchObject({
      state: "transcribing",
      liveTranscript: "partial words",
      recentAction: "Captured audio",
      microphoneActive: false,
      wakeWordPaused: true,
      voiceMuted: false,
      connected: false,
      updatedAt: "2026-07-16T12:00:00.000Z",
    });
  });

  it("normalizes confirmation envelopes and preserves the exact action scope", () => {
    const event = normalizeEvent({
      type: "confirmation_request",
      id: "00000000-0000-4000-8000-000000000001",
      timestamp: "2026-07-16T12:00:00.000Z",
      payload: {
        id: "c-42",
        tool_name: "shutdown_computer",
        prompt: "Restart this computer?",
        action_preview: "Restart Windows immediately",
        arguments: { mode: "restart" },
        risk_level: "high",
        expires_at: "2026-07-16T12:01:00.000Z",
        confirmation_token: "single-use-token",
        action_fingerprint: "sha256:action-digest",
      },
    });

    expect(event).toEqual({
      type: "confirmation_request",
      payload: {
        id: "c-42",
        toolName: "shutdown_computer",
        prompt: "Restart this computer?",
        actionPreview: "Restart Windows immediately",
        arguments: { mode: "restart" },
        riskLevel: "high",
        expiresAt: "2026-07-16T12:01:00.000Z",
        confirmationToken: "single-use-token",
        actionFingerprint: "sha256:action-digest",
      },
    });
    expect(normalizeConfirmation(null).riskLevel).toBe("high");
  });

  it("normalizes settings, tool metadata, and redacted activity records", () => {
    expect(
      normalizeSettings({
        launch_on_startup: true,
        wake_phrase: "Jarvis",
        wake_sensitivity: 0.72,
        tool_permissions: { read_clipboard: "ask_every_time" },
      }),
    ).toMatchObject({
      launchOnStartup: true,
      wakePhrase: "Jarvis",
      wakeSensitivity: 0.72,
      toolPermissions: { read_clipboard: "ask_every_time" },
    });

    expect(
      normalizeTool({
        name: "read_clipboard",
        permission_category: "private_data",
        risk_level: "low",
        requires_confirmation: true,
        timeout_seconds: 3,
        argument_schema: { type: "object", additionalProperties: false },
      }),
    ).toMatchObject({
      name: "read_clipboard",
      permissionCategory: "private_data",
      requiresConfirmation: true,
      timeoutSeconds: 3,
    });

    expect(
      normalizeActivity({
        id: 7,
        created_at: "2026-07-16T12:00:00.000Z",
        user_request: "Read my clipboard",
        assistant_response: "Clipboard access was denied.",
        tool_name: "read_clipboard",
        tool_arguments: { content: "[REDACTED]" },
        risk_level: "low",
        confirmation_result: "denied",
        status: "denied",
      }),
    ).toMatchObject({
      id: 7,
      toolName: "read_clipboard",
      toolArguments: { content: "[REDACTED]" },
      confirmationResult: "denied",
      status: "denied",
    });
  });

  it("presents canonical provider names without changing honest availability", () => {
    expect(
      normalizeProvider({
        name: "openwakeword",
        available: false,
        detail: "Install compatible model assets.",
      }),
    ).toEqual({
      name: "Wake word",
      status: "unavailable",
      detail: "Install compatible model assets.",
    });
  });

  it("distinguishes configured providers whose connection is not yet verified", () => {
    expect(
      normalizeProvider({
        name: "gemini",
        available: false,
        detail: "Configured for gemini-3.1-flash-lite; connection not yet verified",
      }),
    ).toEqual({
      name: "Gemini",
      status: "configured",
      detail: "Configured for gemini-3.1-flash-lite; connection not yet verified",
    });
  });

  it("keeps missing credentials unavailable", () => {
    expect(
      normalizeProvider({
        name: "deepgram",
        available: false,
        detail: "DEEPGRAM_API_KEY is missing",
      }),
    ).toEqual({
      name: "Deepgram",
      status: "unavailable",
      detail: "DEEPGRAM_API_KEY is missing",
    });
  });

  it("returns null for values without an event type", () => {
    expect(normalizeEvent(null)).toBeNull();
    expect(normalizeEvent({ payload: {} })).toBeNull();
  });

  it.each([
    { type: "unregistered_event", payload: {} },
    { type: "partial_transcript", payload: "not-an-object" },
  ])("rejects events outside the shared protocol: $type", (value) => {
    expect(normalizeEvent(value)).toBeNull();
  });

  it("maps canonical wire events to the normalized UI event vocabulary", () => {
    expect(
      normalizeEvent({
        type: "confirmation_decision",
        id: "00000000-0000-4000-8000-000000000002",
        timestamp: "2026-07-16T12:00:00.000Z",
        payload: { id: "c-42", decision: "denied" },
      }),
    ).toEqual({
      type: "confirmation_resolved",
      payload: { id: "c-42", decision: "denied" },
    });
    expect(
      normalizeEvent({
        type: "cancellation",
        id: "00000000-0000-4000-8000-000000000003",
        timestamp: "2026-07-16T12:00:00.000Z",
        payload: {},
      }),
    ).toEqual({
      type: "cancelled",
      payload: { reason: "Operation cancelled." },
    });
  });
});
