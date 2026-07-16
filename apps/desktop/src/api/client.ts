import { invoke } from "@tauri-apps/api/core";
import type {
  ActivityRecord,
  AssistantSettings,
  AssistantSnapshot,
  MicrophoneDevice,
  PermissionLevel,
  ProviderStatus,
  ConfirmationRequest,
  ToolDefinition,
} from "@/types";
import {
  normalizeActivity,
  normalizeConfirmation,
  normalizeProvider,
  normalizeSettings,
  normalizeSnapshot,
  normalizeTool,
} from "./normalizers";

const DEFAULT_URL = "http://127.0.0.1:8765";

const isTauri = (): boolean => typeof window !== "undefined" && !!window.__TAURI_INTERNALS__;

function snakeCaseSettings(patch: Partial<AssistantSettings>): Record<string, unknown> {
  const output: Record<string, unknown> = {};
  const aliases: Record<string, string> = {
    launchOnStartup: "launch_on_startup",
    minimizeToTray: "minimize_to_tray",
    playActivationSound: "play_activation_sound",
    saveConversationHistory: "save_conversation_history",
    developerMode: "developer_mode",
    wakeWordEnabled: "wake_word_enabled",
    wakePhrase: "wake_phrase",
    wakeSensitivity: "wake_word_sensitivity",
    microphoneDevice: "microphone_device",
    pushToTalkShortcut: "push_to_talk_shortcut",
    globalShortcut: "global_shortcut",
    piperExecutablePath: "piper_executable_path",
    piperModelPath: "piper_model_path",
    speechRate: "speech_rate",
    speechVolume: "speech_volume",
    voiceMuted: "voice_muted",
    preferredApplications: "preferred_applications",
  };
  for (const [key, value] of Object.entries(patch)) {
    output[aliases[key] ?? key] = value;
  }
  return output;
}

export class AssistantApi {
  private baseUrl: string;
  private token = "";

  constructor(baseUrl = import.meta.env.VITE_ASSISTANT_URL ?? DEFAULT_URL) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async initialize(): Promise<void> {
    if (isTauri()) {
      const config = await invoke<{ sessionToken: string; baseUrl: string }>("get_backend_config");
      this.token = config.sessionToken;
      this.baseUrl = config.baseUrl.replace(/\/$/, "");
      return;
    }
    this.token = import.meta.env.VITE_ASSISTANT_SESSION_TOKEN ?? "dev-token-change-me";
  }

  websocketUrl(): string {
    return `${this.baseUrl.replace(/^http/, "ws")}/v1/events`;
  }

  sessionToken(): string {
    return this.token;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Assistant-Token": this.token,
        ...init.headers,
      },
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(body?.detail ?? `Assistant backend returned ${response.status}`);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  async state(): Promise<AssistantSnapshot> {
    return normalizeSnapshot(await this.request<unknown>("/v1/state"));
  }

  async settings(): Promise<AssistantSettings> {
    return normalizeSettings(await this.request<unknown>("/v1/settings"));
  }

  async updateSettings(patch: Partial<AssistantSettings>): Promise<AssistantSettings> {
    const value = await this.request<unknown>("/v1/settings", {
      method: "PATCH",
      body: JSON.stringify(snakeCaseSettings(patch)),
    });
    return normalizeSettings(value);
  }

  async tools(): Promise<ToolDefinition[]> {
    const value = await this.request<unknown>("/v1/tools");
    const list = Array.isArray(value) ? value : ((value as { tools?: unknown[] }).tools ?? []);
    return list.map(normalizeTool);
  }

  async updateTool(
    name: string,
    patch: { enabled?: boolean; permission?: PermissionLevel },
  ): Promise<ToolDefinition> {
    return normalizeTool(
      await this.request<unknown>(`/v1/tools/${encodeURIComponent(name)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    );
  }

  async history(): Promise<ActivityRecord[]> {
    const value = await this.request<unknown>("/v1/history?limit=100");
    const list = Array.isArray(value) ? value : ((value as { items?: unknown[] }).items ?? []);
    return list.map(normalizeActivity);
  }

  async pendingConfirmations(): Promise<ConfirmationRequest[]> {
    const value = await this.request<unknown>("/v1/confirmations/pending");
    if (!Array.isArray(value)) return [];
    return value
      .map(normalizeConfirmation)
      .filter(
        (request) =>
          Boolean(request.id && request.confirmationToken && request.actionFingerprint) &&
          new Date(request.expiresAt).getTime() > Date.now(),
      );
  }

  async providers(): Promise<ProviderStatus[]> {
    const value = await this.request<unknown>("/v1/providers/status");
    const list = Array.isArray(value)
      ? value
      : ((value as { providers?: unknown[] }).providers ?? []);
    return list.map(normalizeProvider);
  }

  async microphones(): Promise<MicrophoneDevice[]> {
    const value = await this.request<unknown>("/v1/audio/devices");
    const list = Array.isArray(value) ? value : ((value as { devices?: unknown[] }).devices ?? []);
    return list.map((entry) => {
      const item = entry as Record<string, unknown>;
      const rawId = item.id ?? item.device_id;
      const rawName = item.name;
      return {
        id: typeof rawId === "string" || typeof rawId === "number" ? String(rawId) : "",
        name: typeof rawName === "string" ? rawName : "Unknown microphone",
        isDefault: Boolean(item.isDefault ?? item.is_default ?? false),
      };
    });
  }

  startListening(): Promise<void> {
    return this.request("/v1/listen/start", { method: "POST", body: "{}" });
  }

  stopListening(): Promise<void> {
    return this.request("/v1/listen/stop", { method: "POST", body: "{}" });
  }

  cancel(): Promise<void> {
    return this.request("/v1/cancel", { method: "POST", body: "{}" });
  }

  sendCommand(text: string): Promise<void> {
    return this.request("/v1/command", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
  }

  decideConfirmation(request: ConfirmationRequest, approved: boolean): Promise<void> {
    return this.request(`/v1/confirmations/${encodeURIComponent(request.id)}/decide`, {
      method: "POST",
      body: JSON.stringify({
        decision: approved ? "yes" : "no",
        confirmation_token: request.confirmationToken,
        action_fingerprint: request.actionFingerprint,
      }),
    });
  }

  muteVoice(muted: boolean): Promise<void> {
    return this.request("/v1/voice/mute", {
      method: "POST",
      body: JSON.stringify({ muted }),
    });
  }

  async clearData(): Promise<void> {
    await this.request("/v1/data", { method: "DELETE" });
  }
}
