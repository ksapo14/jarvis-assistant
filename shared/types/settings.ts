import type { PermissionLevel } from "./protocol";

export interface AssistantSettings {
  launchOnStartup: boolean;
  minimizeToTray: boolean;
  playActivationSound: boolean;
  saveConversationHistory: boolean;
  developerMode: boolean;
  wakeWordEnabled: boolean;
  wakePhrase: string;
  wakeSensitivity: number;
  microphoneDevice: string | null;
  pushToTalkShortcut: string;
  globalShortcut: string;
  piperExecutablePath: string;
  piperModelPath: string;
  speechRate: number;
  speechVolume: number;
  voiceMuted: boolean;
  preferredApplications: Record<string, string>;
  toolPermissions: Record<string, PermissionLevel>;
}

export interface ProviderStatus {
  name: string;
  status: "ready" | "configured" | "not_configured" | "unavailable" | "error";
  detail: string;
}
