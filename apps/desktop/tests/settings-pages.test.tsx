import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GeneralPage } from "@/pages/GeneralPage";
import { VoicePage } from "@/pages/VoicePage";
import { useAssistant } from "@/state/context";
import type { AssistantSettings } from "@/types";

vi.mock("@/state/context", () => ({
  useAssistant: vi.fn(),
}));

const useAssistantMock = vi.mocked(useAssistant);
const updateSettings = vi.fn(() => Promise.resolve());
const reportError = vi.fn();

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
  preferredApplications: {},
  toolPermissions: {},
};

describe("settings text drafts", () => {
  beforeEach(() => {
    updateSettings.mockClear();
    reportError.mockClear();
    useAssistantMock.mockReturnValue({
      settings,
      microphones: [],
      updateSettings,
      reportError,
      clearData: vi.fn(() => Promise.resolve()),
    } as unknown as ReturnType<typeof useAssistant>);
  });

  it("keeps the working global shortcut while an invalid draft is being edited", async () => {
    const user = userEvent.setup();
    render(<GeneralPage />);
    const input = screen.getByRole("textbox", { name: /open assistant shortcut/i });

    await user.clear(input);
    await user.type(input, "Ctrl+");
    expect(updateSettings).not.toHaveBeenCalled();

    await user.tab();
    expect(updateSettings).not.toHaveBeenCalled();
    expect(reportError).toHaveBeenCalledWith(expect.stringContaining("was not saved"));

    await user.click(input);
    await user.clear(input);
    await user.type(input, "Ctrl+Alt+J");
    await user.keyboard("{Enter}");
    expect(updateSettings).toHaveBeenCalledWith({ globalShortcut: "Ctrl+Alt+J" });
  });

  it("commits wake, push-to-talk, and Piper drafts only after blur or Enter", async () => {
    const user = userEvent.setup();
    render(<VoicePage />);

    const wakePhrase = screen.getByRole("textbox", { name: /wake phrase/i });
    await user.clear(wakePhrase);
    await user.type(wakePhrase, "Computer");
    expect(updateSettings).not.toHaveBeenCalled();
    await user.tab();
    expect(updateSettings).toHaveBeenLastCalledWith({ wakePhrase: "Computer" });

    updateSettings.mockClear();
    const pushToTalk = screen.getByRole("textbox", { name: /push-to-talk shortcut/i });
    await user.clear(pushToTalk);
    await user.type(pushToTalk, "Ctrl");
    await user.tab();
    expect(updateSettings).not.toHaveBeenCalled();
    expect(reportError).toHaveBeenCalledWith(expect.stringContaining("was not saved"));

    const executable = screen.getByRole("textbox", { name: /piper executable/i });
    await user.type(executable, "C:\\Tools\\piper.exe");
    expect(updateSettings).not.toHaveBeenCalled();
    await user.tab();
    expect(updateSettings).toHaveBeenLastCalledWith({
      piperExecutablePath: "C:\\Tools\\piper.exe",
    });

    updateSettings.mockClear();
    const model = screen.getByRole("textbox", { name: /voice model/i });
    await user.type(model, "C:\\Voices\\jarvis.onnx");
    expect(updateSettings).not.toHaveBeenCalled();
    await user.keyboard("{Enter}");
    expect(updateSettings).toHaveBeenCalledWith({
      piperModelPath: "C:\\Voices\\jarvis.onnx",
    });
  });
});
