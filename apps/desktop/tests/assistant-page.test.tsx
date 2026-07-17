import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantPage } from "@/pages/AssistantPage";
import { useAssistant } from "@/state/context";
import type { AssistantSettings, AssistantSnapshot } from "@/types";

vi.mock("@/state/context", () => ({
  useAssistant: vi.fn(),
}));

const useAssistantMock = vi.mocked(useAssistant);

const settings: AssistantSettings = {
  launchOnStartup: false,
  minimizeToTray: true,
  playActivationSound: true,
  saveConversationHistory: true,
  developerMode: false,
  wakeWordEnabled: true,
  wakePhrase: "Jarvis",
  wakeSensitivity: 0.55,
  microphoneDevice: null,
  pushToTalkShortcut: "Ctrl+Space",
  globalShortcut: "Ctrl+Shift+J",
  piperExecutablePath: "",
  piperModelPath: "",
  speechRate: 1,
  speechVolume: 0.9,
  voiceMuted: false,
  preferredApplications: {},
  toolPermissions: {},
};

const idleSnapshot: AssistantSnapshot = {
  state: "idle",
  detail: "Ready for a command",
  liveTranscript: "",
  finalTranscript: "",
  response: "",
  recentAction: "",
  microphoneActive: false,
  wakeWordPaused: false,
  voiceMuted: false,
  connected: true,
  updatedAt: "2026-07-16T12:00:00.000Z",
};

function mockContext(
  snapshot: AssistantSnapshot = idleSnapshot,
  controlPending: "starting" | "stopping" | "cancelling" | null = null,
) {
  const actions = {
    toggleListening: vi.fn(() => Promise.resolve()),
    cancel: vi.fn(() => Promise.resolve()),
    sendCommand: vi.fn(() => Promise.resolve()),
    decideConfirmation: vi.fn(() => Promise.resolve()),
    updateSettings: vi.fn(() => Promise.resolve()),
    updateTool: vi.fn(() => Promise.resolve()),
    clearData: vi.fn(() => Promise.resolve()),
    refreshHistory: vi.fn(() => Promise.resolve()),
    dismissError: vi.fn(),
    reportError: vi.fn(),
  };
  useAssistantMock.mockReturnValue({
    snapshot,
    confirmation: null,
    settings,
    tools: [],
    history: [],
    providers: [],
    microphones: [],
    controlPending,
    loading: false,
    uiError: null,
    ...actions,
  });
  return actions;
}

describe("AssistantPage", () => {
  beforeEach(() => {
    useAssistantMock.mockReset();
  });

  it("renders live listening state and exposes cancellation and stop controls", async () => {
    const user = userEvent.setup();
    const actions = mockContext({
      ...idleSnapshot,
      state: "listening",
      detail: "Listening for your request",
      liveTranscript: "open my project folder",
      microphoneActive: true,
    });

    render(<AssistantPage />);

    expect(screen.getByRole("status")).toHaveTextContent("Listening");
    expect(screen.getByText("open my project folder")).toBeVisible();
    expect(screen.getByText("live")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Type a command" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Stop listening" }));
    await user.click(screen.getByRole("button", { name: /cancel operation/i }));

    expect(actions.toggleListening).toHaveBeenCalledOnce();
    expect(actions.cancel).toHaveBeenCalledOnce();
  });

  it("trims and sends a typed command while idle", async () => {
    const user = userEvent.setup();
    const actions = mockContext();
    render(<AssistantPage />);

    const input = screen.getByRole("textbox", { name: "Type a command" });
    await user.type(input, "  what time is it?  ");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(actions.sendCommand).toHaveBeenCalledWith("what time is it?");
    expect(input).toHaveValue("");
  });

  it("does not expose push-to-talk when the backend is disconnected", () => {
    mockContext({ ...idleSnapshot, connected: false, detail: "Backend disconnected" });
    render(<AssistantPage />);

    expect(screen.getByRole("button", { name: "Start push to talk" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Type a command" })).toBeDisabled();
  });

  it("stops speech without presenting the action as a restart", async () => {
    const user = userEvent.setup();
    const actions = mockContext({
      ...idleSnapshot,
      state: "speaking",
      detail: "Speaking",
    });
    render(<AssistantPage />);

    await user.click(screen.getByRole("button", { name: "Stop speaking" }));

    expect(actions.toggleListening).toHaveBeenCalledOnce();
  });

  it("disables repeated controls while cancellation is pending", () => {
    mockContext(
      {
        ...idleSnapshot,
        state: "speaking",
        detail: "Cancelling the current operation…",
      },
      "cancelling",
    );
    render(<AssistantPage />);

    expect(screen.getByRole("button", { name: "Cancelling operation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled();
  });
});
