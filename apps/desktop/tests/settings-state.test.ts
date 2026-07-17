import { describe, expect, it } from "vitest";
import { normalizeEvent } from "@/api/normalizers";
import { mergeSettingsUpdate } from "@/settings-state";
import type { AssistantSettings } from "@/types";

const settings: AssistantSettings = {
  launchOnStartup: false,
  minimizeToTray: true,
  playActivationSound: true,
  saveConversationHistory: true,
  developerMode: false,
  wakeWordEnabled: true,
  wakePhrase: "Hey Jarvis",
  wakeSensitivity: 0.55,
  microphoneDevice: null,
  pushToTalkShortcut: "Ctrl+Space",
  globalShortcut: "Ctrl+Shift+J",
  piperExecutablePath: "",
  piperModelPath: "",
  speechRate: 1,
  speechVolume: 0.9,
  voiceMuted: false,
  preferredApplications: { editor: "notepad.exe" },
  toolPermissions: { read_clipboard: "ask_every_time" },
};

function event(payload: Record<string, unknown>) {
  return normalizeEvent({
    type: "settings_updated",
    id: "00000000-0000-4000-8000-000000000004",
    timestamp: "2026-07-16T12:00:00.000Z",
    payload,
  });
}

describe("settings update events", () => {
  it("normalizes tray-originated snake-case settings and merges only those fields", () => {
    const normalized = event({ wake_word_enabled: false, voice_muted: true });
    expect(normalized).toEqual({
      type: "settings_updated",
      payload: { wakeWordEnabled: false, voiceMuted: true },
    });
    if (!normalized || normalized.type !== "settings_updated") throw new Error("bad fixture");

    expect(mergeSettingsUpdate(settings, normalized.payload)).toEqual({
      ...settings,
      wakeWordEnabled: false,
      voiceMuted: true,
    });
  });

  it("rejects invalid known settings rather than corrupting UI state", () => {
    expect(event({ wake_word_enabled: "false" })).toBeNull();
    expect(event({ speech_volume: 2 })).toBeNull();
    expect(event({ wake_phrase: "" })).toBeNull();
    expect(event({ untrusted_future_field: true })).toBeNull();
  });

  it("validates and applies complete map-valued setting fields", () => {
    const normalized = event({
      preferred_applications: { browser: "msedge.exe" },
      tool_permissions: { take_screenshot: "disabled" },
    });
    if (!normalized || normalized.type !== "settings_updated") throw new Error("bad fixture");

    expect(mergeSettingsUpdate(settings, normalized.payload)).toMatchObject({
      preferredApplications: { browser: "msedge.exe" },
      toolPermissions: { take_screenshot: "disabled" },
    });
  });
});
