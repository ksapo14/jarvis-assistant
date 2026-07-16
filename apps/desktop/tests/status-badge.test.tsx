import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge, stateLabel } from "@/components/StatusBadge";
import { ASSISTANT_STATES, type AssistantState } from "@shared/types/protocol";

const expectedLabels: Record<AssistantState, string> = {
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

describe("StatusBadge", () => {
  it.each(ASSISTANT_STATES)("renders the %s state accessibly", (state) => {
    render(<StatusBadge state={state} />);

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(expectedLabels[state]);
    expect(status).toHaveClass(`state-${state}`);
    expect(stateLabel(state)).toBe(expectedLabels[state]);
  });
});
