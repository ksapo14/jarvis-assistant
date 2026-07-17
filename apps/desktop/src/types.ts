export type {
  ActivityRecord,
  AssistantSnapshot,
  AssistantState,
  ClientMessage,
  ConfirmationRequest,
  PermissionLevel,
  ProviderStatus,
  RiskLevel,
  ToolDefinition,
} from "@shared/types";
export type { NormalizedServerEvent as ServerEvent } from "@shared/types";
export type { AssistantSettings } from "@shared/types";

export interface MicrophoneDevice {
  id: string;
  name: string;
  isDefault: boolean;
}

export interface BackendHealth {
  status: "ok" | "degraded";
  version: string;
  environment: string;
}
