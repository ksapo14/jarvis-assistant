import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ActivityPage } from "@/pages/ActivityPage";
import { useAssistant } from "@/state/context";

vi.mock("@/state/context", () => ({ useAssistant: vi.fn() }));

const useAssistantMock = vi.mocked(useAssistant);
const refreshHistory = vi.fn(() => Promise.resolve());

describe("ActivityPage", () => {
  beforeEach(() => {
    refreshHistory.mockClear();
    useAssistantMock.mockReturnValue({
      history: [
        {
          id: 12,
          createdAt: "2026-07-16T12:00:00.000Z",
          userRequest: "Open the project notes",
          assistantResponse: "I opened the project notes.",
          toolName: "open_file_or_folder",
          toolArguments: { path: "[REDACTED_PATH]" },
          toolResult: { success: true, message: "Opened the selected document." },
          riskLevel: "low",
          confirmationResult: "approved",
          status: "success",
        },
      ],
      refreshHistory,
    } as unknown as ReturnType<typeof useAssistant>);
  });

  it("shows the complete redacted audit record on demand", async () => {
    const user = userEvent.setup();
    render(<ActivityPage />);

    expect(screen.getByText("Open the project notes")).toBeVisible();
    expect(screen.getByText("I opened the project notes.")).toBeVisible();
    expect(screen.getByText("open_file_or_folder")).toBeVisible();
    expect(screen.getByText("Confirmation: approved")).toBeVisible();

    await user.click(screen.getByText("Redacted tool details"));
    expect(screen.getByText(/REDACTED_PATH/)).toBeVisible();
    expect(screen.getByText(/Opened the selected document/)).toBeVisible();
    expect(refreshHistory).toHaveBeenCalled();
  });
});
