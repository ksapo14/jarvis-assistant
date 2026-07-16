import type {
  ActivityRecord,
  AssistantSettings,
  AssistantSnapshot,
  ConfirmationRequest,
  ProviderStatus,
  ServerEvent,
  ToolDefinition,
} from "@/types";
import { ASSISTANT_STATES } from "@shared/types/protocol";

type JsonObject = Record<string, unknown>;

const asObject = (value: unknown): JsonObject =>
  typeof value === "object" && value !== null ? (value as JsonObject) : {};

const isObject = (value: unknown): value is JsonObject =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isString = (value: unknown): value is string => typeof value === "string";

const isAssistantState = (value: unknown): value is AssistantSnapshot["state"] =>
  isString(value) && (ASSISTANT_STATES as readonly string[]).includes(value);

const read = <T>(source: JsonObject, camel: string, snake: string, fallback: T): T =>
  (source[camel] ?? source[snake] ?? fallback) as T;

function aliasedValue(
  source: JsonObject,
  camel: string,
  snake: string,
): { present: boolean; value: unknown } {
  if (Object.prototype.hasOwnProperty.call(source, camel)) {
    return { present: true, value: source[camel] };
  }
  if (Object.prototype.hasOwnProperty.call(source, snake)) {
    return { present: true, value: source[snake] };
  }
  return { present: false, value: undefined };
}

export function normalizeSettingsPatch(value: unknown): Record<string, unknown> | null {
  if (!isObject(value)) return null;
  const output: Record<string, unknown> = {};

  const take = (
    camel: keyof AssistantSettings,
    snake: string,
    validate: (candidate: unknown) => boolean,
  ): boolean => {
    const candidate = aliasedValue(value, camel, snake);
    if (!candidate.present) return true;
    if (!validate(candidate.value)) return false;
    output[camel] = candidate.value;
    return true;
  };

  const finiteNumber = (candidate: unknown): candidate is number =>
    typeof candidate === "number" && Number.isFinite(candidate);
  const unitInterval = (candidate: unknown): candidate is number =>
    finiteNumber(candidate) && candidate >= 0 && candidate <= 1;
  const permissionMap = (candidate: unknown): boolean =>
    isObject(candidate) &&
    Object.values(candidate).every(
      (permission) =>
        typeof permission === "string" &&
        ["disabled", "ask_every_time", "allow_session", "always_allow"].includes(permission),
    );
  const stringMap = (candidate: unknown): boolean =>
    isObject(candidate) && Object.values(candidate).every((entry) => typeof entry === "string");

  const valid = [
    take("launchOnStartup", "launch_on_startup", (candidate) => typeof candidate === "boolean"),
    take("minimizeToTray", "minimize_to_tray", (candidate) => typeof candidate === "boolean"),
    take("playActivationSound", "play_activation_sound", (candidate) =>
      Boolean(typeof candidate === "boolean"),
    ),
    take("saveConversationHistory", "save_conversation_history", (candidate) =>
      Boolean(typeof candidate === "boolean"),
    ),
    take("developerMode", "developer_mode", (candidate) => typeof candidate === "boolean"),
    take("wakeWordEnabled", "wake_word_enabled", (candidate) => typeof candidate === "boolean"),
    take("wakePhrase", "wake_phrase", (candidate) =>
      Boolean(typeof candidate === "string" && candidate.trim()),
    ),
    take("wakeSensitivity", "wake_sensitivity", unitInterval),
    take(
      "microphoneDevice",
      "microphone_device",
      (candidate) => candidate === null || typeof candidate === "string",
    ),
    take("pushToTalkShortcut", "push_to_talk_shortcut", (candidate) =>
      Boolean(typeof candidate === "string" && candidate.trim()),
    ),
    take("globalShortcut", "global_shortcut", (candidate) =>
      Boolean(typeof candidate === "string" && candidate.trim()),
    ),
    take("piperExecutablePath", "piper_executable_path", (candidate) =>
      Boolean(typeof candidate === "string"),
    ),
    take("piperModelPath", "piper_model_path", (candidate) =>
      Boolean(typeof candidate === "string"),
    ),
    take(
      "speechRate",
      "speech_rate",
      (candidate) => finiteNumber(candidate) && candidate >= 0.5 && candidate <= 2,
    ),
    take("speechVolume", "speech_volume", unitInterval),
    take("voiceMuted", "voice_muted", (candidate) => typeof candidate === "boolean"),
    take("preferredApplications", "preferred_applications", stringMap),
    take("toolPermissions", "tool_permissions", permissionMap),
  ].every(Boolean);

  return valid && Object.keys(output).length > 0 ? output : null;
}

export function normalizeSnapshot(value: unknown): AssistantSnapshot {
  const item = asObject(value);
  return {
    state: read(item, "state", "state", "idle"),
    detail: read(item, "detail", "detail", "Ready"),
    liveTranscript: read(item, "liveTranscript", "live_transcript", ""),
    finalTranscript: read(item, "finalTranscript", "final_transcript", ""),
    response: read(item, "response", "response", ""),
    recentAction: read(item, "recentAction", "recent_action", ""),
    microphoneActive: read(item, "microphoneActive", "microphone_active", false),
    wakeWordPaused: read(item, "wakeWordPaused", "wake_word_paused", false),
    voiceMuted: read(item, "voiceMuted", "voice_muted", false),
    connected: read(item, "connected", "connected", true),
    ...(typeof item.error === "string" ? { error: item.error } : {}),
    updatedAt: read(item, "updatedAt", "updated_at", new Date().toISOString()),
  };
}

export function normalizeConfirmation(value: unknown): ConfirmationRequest {
  const item = asObject(value);
  const toolCall = asObject(item.toolCall ?? item.tool_call);
  const argumentsValue = toolCall.arguments ?? item.arguments;
  return {
    id: read(item, "id", "id", ""),
    toolName: read(item, "toolName", "tool_name", read(toolCall, "name", "name", "unknown")),
    prompt: read(item, "prompt", "prompt", "This action needs confirmation."),
    actionPreview: read(
      item,
      "actionPreview",
      "action_preview",
      read(item, "prompt", "prompt", ""),
    ),
    arguments: isObject(argumentsValue) ? argumentsValue : {},
    riskLevel: read(item, "riskLevel", "risk_level", "high"),
    expiresAt: read(item, "expiresAt", "expires_at", new Date().toISOString()),
    confirmationToken: read(item, "confirmationToken", "confirmation_token", ""),
    actionFingerprint: read(item, "actionFingerprint", "action_fingerprint", ""),
  };
}

export function normalizeEvent(value: unknown): ServerEvent | null {
  if (
    !isObject(value) ||
    !isObject(value.payload) ||
    !isString(value.type) ||
    !isString(value.id) ||
    !isString(value.timestamp)
  ) {
    return null;
  }
  const payload = value.payload;
  const timestamp = value.timestamp;

  if (value.type === "status_changed") {
    if (!isAssistantState(payload.state)) return null;
    const detail = payload.detail == null ? "" : payload.detail;
    if (!isString(detail)) return null;
    return { type: "status_changed", payload: { state: payload.state, detail, timestamp } };
  }
  if (value.type === "partial_transcript" || value.type === "final_transcript") {
    if (!isString(payload.text)) return null;
    return { type: value.type, payload: { text: payload.text } };
  }
  if (value.type === "assistant_response") {
    if (!isString(payload.text)) return null;
    const spokenText = payload.spokenText ?? payload.spoken_text ?? payload.text;
    if (!isString(spokenText)) return null;
    return { type: "assistant_response", payload: { text: payload.text, spokenText } };
  }
  if (value.type === "tool_proposal") {
    const toolCall = asObject(payload.toolCall ?? payload.tool_call);
    const name = payload.name ?? toolCall.name;
    const argumentsValue = payload.arguments ?? toolCall.arguments;
    if (!isString(name) || !isObject(argumentsValue)) return null;
    return { type: "tool_proposal", payload: { name, arguments: argumentsValue } };
  }
  if (value.type === "confirmation_request") {
    const confirmation = normalizeConfirmation(payload);
    if (
      !confirmation.id ||
      !confirmation.confirmationToken ||
      !confirmation.actionFingerprint ||
      !["low", "medium", "high"].includes(confirmation.riskLevel)
    ) {
      return null;
    }
    return { type: "confirmation_request", payload: confirmation };
  }
  if (value.type === "confirmation_resolved" || value.type === "confirmation_decision") {
    if (!isString(payload.id) || !isString(payload.decision)) return null;
    return {
      type: "confirmation_resolved",
      payload: { id: payload.id, decision: payload.decision },
    };
  }
  if (value.type === "tool_execution_result") {
    return { type: "tool_execution_result", payload };
  }
  if (value.type === "settings_updated") {
    const settings = normalizeSettingsPatch(payload);
    return settings ? { type: "settings_updated", payload: settings } : null;
  }
  if (value.type === "cancelled" || value.type === "cancellation") {
    const reason = payload.reason;
    if (reason !== undefined && !isString(reason)) return null;
    return { type: "cancelled", payload: { reason: reason ?? "Operation cancelled." } };
  }
  if (value.type === "error") {
    if (!isString(payload.code) || !isString(payload.message)) return null;
    return {
      type: "error",
      payload: {
        code: payload.code,
        message: payload.message,
        recoverable: typeof payload.recoverable === "boolean" ? payload.recoverable : true,
      },
    };
  }
  return null;
}

export function normalizeSettings(value: unknown): AssistantSettings {
  const item = asObject(value);
  const permissions = read<JsonObject>(item, "toolPermissions", "tool_permissions", {});
  const preferredApplications = read<JsonObject>(
    item,
    "preferredApplications",
    "preferred_applications",
    {},
  );
  return {
    launchOnStartup: read(item, "launchOnStartup", "launch_on_startup", false),
    minimizeToTray: read(item, "minimizeToTray", "minimize_to_tray", true),
    playActivationSound: read(item, "playActivationSound", "play_activation_sound", true),
    saveConversationHistory: read(
      item,
      "saveConversationHistory",
      "save_conversation_history",
      read(item, "saveHistory", "save_history", true),
    ),
    developerMode: read(item, "developerMode", "developer_mode", false),
    wakeWordEnabled: read(item, "wakeWordEnabled", "wake_word_enabled", true),
    wakePhrase: read(
      item,
      "wakePhrase",
      "wake_phrase",
      read(item, "wakeWordPhrase", "wake_word_phrase", "Hey Jarvis"),
    ),
    wakeSensitivity: read(
      item,
      "wakeSensitivity",
      "wake_sensitivity",
      read(item, "wakeWordSensitivity", "wake_word_sensitivity", 0.55),
    ),
    microphoneDevice: read<string | null>(item, "microphoneDevice", "microphone_device", null),
    pushToTalkShortcut: read(item, "pushToTalkShortcut", "push_to_talk_shortcut", "Ctrl+Space"),
    globalShortcut: read(item, "globalShortcut", "global_shortcut", "Ctrl+Shift+J"),
    piperExecutablePath: read(item, "piperExecutablePath", "piper_executable_path", ""),
    piperModelPath: read(item, "piperModelPath", "piper_model_path", ""),
    speechRate: read(item, "speechRate", "speech_rate", 1),
    speechVolume: read(item, "speechVolume", "speech_volume", 0.9),
    voiceMuted: read(item, "voiceMuted", "voice_muted", false),
    preferredApplications: preferredApplications as AssistantSettings["preferredApplications"],
    toolPermissions: permissions as AssistantSettings["toolPermissions"],
  };
}

export function normalizeTool(value: unknown): ToolDefinition {
  const item = asObject(value);
  return {
    name: read(item, "name", "name", "unknown"),
    description: read(item, "description", "description", "No description provided."),
    permissionCategory: read(item, "permissionCategory", "permission_category", "general"),
    riskLevel: read(item, "riskLevel", "risk_level", "low"),
    requiresConfirmation: read(
      item,
      "requiresConfirmation",
      "requires_confirmation",
      read(item, "confirmationRequired", "confirmation_required", false),
    ),
    enabled: read(item, "enabled", "enabled", true),
    permission: read(item, "permission", "permission", "ask_every_time"),
    timeoutSeconds: read(item, "timeoutSeconds", "timeout_seconds", 15),
    argumentSchema: read(item, "argumentSchema", "argument_schema", {}),
  };
}

export function normalizeActivity(value: unknown): ActivityRecord {
  const item = asObject(value);
  const toolArguments = read<JsonObject | undefined>(
    item,
    "toolArguments",
    "tool_arguments",
    undefined,
  );
  const toolResult = read<JsonObject | undefined>(item, "toolResult", "tool_result", undefined);
  return {
    id: read(item, "id", "id", 0),
    createdAt: read(item, "createdAt", "created_at", new Date().toISOString()),
    userRequest: read(item, "userRequest", "user_request", ""),
    assistantResponse: read(item, "assistantResponse", "assistant_response", ""),
    ...(typeof item.toolName === "string" || typeof item.tool_name === "string"
      ? { toolName: read(item, "toolName", "tool_name", "") }
      : {}),
    ...(toolArguments ? { toolArguments } : {}),
    ...(toolResult ? { toolResult } : {}),
    ...(item.riskLevel || item.risk_level
      ? { riskLevel: read(item, "riskLevel", "risk_level", "low") }
      : {}),
    ...(item.confirmationResult || item.confirmation_result
      ? {
          confirmationResult: read(item, "confirmationResult", "confirmation_result", "cancelled"),
        }
      : {}),
    status: read(item, "status", "status", "success"),
  };
}

export function normalizeProvider(value: unknown): ProviderStatus {
  const item = asObject(value);
  const rawName = read(item, "name", "name", "Provider");
  const providerLabels: Record<string, string> = {
    deepgram: "Deepgram",
    gemini: "Gemini",
    piper: "Piper",
    openwakeword: "Wake word",
    willow: "Willow WIS",
    "mock-stt": "Mock transcription",
    "mock-gemini": "Mock Gemini",
    "mock-piper": "Mock speech",
    "mock-wake-word": "Mock wake word",
  };
  const explicitStatus = item.status;
  const status =
    typeof explicitStatus === "string"
      ? explicitStatus
      : item.available === true
        ? "ready"
        : "unavailable";
  return {
    name: providerLabels[rawName] ?? rawName,
    status: status as ProviderStatus["status"],
    detail: read(item, "detail", "detail", "No status available"),
  };
}
