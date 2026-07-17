export const ASSISTANT_STATES = [
  "idle",
  "wake_word_detected",
  "listening",
  "transcribing",
  "thinking",
  "waiting_for_confirmation",
  "executing",
  "speaking",
  "error",
] as const;

export type AssistantState = (typeof ASSISTANT_STATES)[number];
export type RiskLevel = "low" | "medium" | "high";
export type PermissionLevel = "disabled" | "ask_every_time" | "allow_session" | "always_allow";

export interface AssistantSnapshot {
  state: AssistantState;
  detail: string;
  liveTranscript: string;
  finalTranscript: string;
  response: string;
  recentAction: string;
  microphoneActive: boolean;
  wakeWordPaused: boolean;
  voiceMuted: boolean;
  connected: boolean;
  error?: string;
  updatedAt: string;
}

export interface ToolDefinition {
  name: string;
  description: string;
  permissionCategory: string;
  riskLevel: RiskLevel;
  requiresConfirmation: boolean;
  enabled: boolean;
  permission: PermissionLevel;
  timeoutSeconds: number;
  argumentSchema: Record<string, unknown>;
}

export interface ConfirmationRequest {
  id: string;
  toolName: string;
  prompt: string;
  actionPreview: string;
  arguments: Record<string, unknown>;
  riskLevel: RiskLevel;
  expiresAt: string;
  /** One-use anti-replay value. Keep in memory only; never display or log it. */
  confirmationToken: string;
  /** Canonical tool+argument digest that prevents changed-action approval. */
  actionFingerprint: string;
}

export interface ActivityRecord {
  id: number;
  createdAt: string;
  userRequest: string;
  assistantResponse: string;
  toolName?: string;
  toolArguments?: Record<string, unknown>;
  toolResult?: Record<string, unknown>;
  riskLevel?: RiskLevel;
  confirmationResult?: "approved" | "denied" | "expired" | "cancelled";
  status: "success" | "denied" | "error" | "cancelled";
}

export interface WireEventEnvelope<TType extends string, TPayload extends Record<string, unknown>> {
  type: TType;
  id: string;
  timestamp: string;
  payload: TPayload;
}

/** Canonical event envelope emitted by the Python backend. */
export type ServerEvent =
  | WireEventEnvelope<"status_changed", Record<string, unknown>>
  | WireEventEnvelope<"partial_transcript", Record<string, unknown>>
  | WireEventEnvelope<"final_transcript", Record<string, unknown>>
  | WireEventEnvelope<"assistant_response", Record<string, unknown>>
  | WireEventEnvelope<"tool_proposal", Record<string, unknown>>
  | WireEventEnvelope<"confirmation_request", Record<string, unknown>>
  | WireEventEnvelope<"confirmation_decision", Record<string, unknown>>
  | WireEventEnvelope<"tool_execution_result", Record<string, unknown>>
  | WireEventEnvelope<"settings_updated", Record<string, unknown>>
  | WireEventEnvelope<"cancellation", Record<string, unknown>>
  | WireEventEnvelope<"error", Record<string, unknown>>;

/** Strict, camel-cased event accepted by the React state layer after normalization. */
export type NormalizedServerEvent =
  | { type: "snapshot"; payload: AssistantSnapshot }
  | { type: "status_changed"; payload: { state: AssistantState; detail: string; timestamp: string } }
  | { type: "partial_transcript"; payload: { text: string } }
  | { type: "final_transcript"; payload: { text: string } }
  | { type: "assistant_response"; payload: { text: string; spokenText: string } }
  | { type: "tool_proposal"; payload: { name: string; arguments: Record<string, unknown> } }
  | { type: "confirmation_request"; payload: ConfirmationRequest }
  | { type: "confirmation_resolved"; payload: { id: string; decision: string } }
  | { type: "tool_execution_result"; payload: Record<string, unknown> }
  | { type: "settings_updated"; payload: Record<string, unknown> }
  | { type: "cancelled"; payload: { reason: string } }
  | { type: "error"; payload: { code: string; message: string; recoverable: boolean } };

export type ClientMessage =
  | { type: "authenticate"; token: string }
  | { type: "ping" }
  | { type: "cancel" }
  | { type: "start_listening" };
