import type { AssistantState } from "@/types";

const stateLabels: Record<AssistantState, string> = {
  idle: "Idle",
  wake_word_detected: "Wake word detected",
  listening: "Listening",
  transcribing: "Transcribing",
  thinking: "Thinking",
  waiting_for_confirmation: "Waiting for confirmation",
  executing: "Executing",
  speaking: "Speaking",
  error: "Error",
};

export function StatusBadge({ state }: { state: AssistantState }) {
  return (
    <span className={`status-badge state-${state}`} role="status">
      <i />
      {stateLabels[state]}
    </span>
  );
}

export function stateLabel(state: AssistantState): string {
  return stateLabels[state];
}
