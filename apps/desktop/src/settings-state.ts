import type { AssistantSettings } from "@/types";

/** Applies an already validated WebSocket patch without resetting omitted settings. */
export function mergeSettingsUpdate(
  current: AssistantSettings,
  patch: Record<string, unknown>,
): AssistantSettings {
  const update = patch as Partial<AssistantSettings>;
  return {
    ...current,
    ...update,
  };
}
