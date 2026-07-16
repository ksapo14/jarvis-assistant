import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToolsPage } from "@/pages/ToolsPage";
import { useAssistant } from "@/state/context";
import type { ToolDefinition } from "@/types";

vi.mock("@/state/context", () => ({
  useAssistant: vi.fn(),
}));

const useAssistantMock = vi.mocked(useAssistant);
const updateTool = vi.fn(() => Promise.resolve());

const tools: ToolDefinition[] = [
  {
    name: "open_website",
    description: "Open a validated URL in the default browser.",
    permissionCategory: "browser",
    riskLevel: "low",
    requiresConfirmation: false,
    enabled: true,
    permission: "always_allow",
    timeoutSeconds: 10,
    argumentSchema: { type: "object" },
  },
  {
    name: "delete_path",
    description: "Delete one explicitly selected file or folder.",
    permissionCategory: "filesystem",
    riskLevel: "high",
    requiresConfirmation: true,
    enabled: true,
    permission: "ask_every_time",
    timeoutSeconds: 20,
    argumentSchema: { type: "object" },
  },
];

describe("ToolsPage", () => {
  beforeEach(() => {
    updateTool.mockClear();
    useAssistantMock.mockReturnValue({ tools, updateTool } as unknown as ReturnType<
      typeof useAssistant
    >);
  });

  it("updates enablement and permission through typed tool mutations", async () => {
    const user = userEvent.setup();
    render(<ToolsPage />);

    await user.click(screen.getByRole("switch", { name: "Disable open_website" }));
    expect(updateTool).toHaveBeenCalledWith("open_website", { enabled: false });

    const websiteCard = screen.getByRole("heading", { name: "open website" }).closest("article");
    expect(websiteCard).not.toBeNull();
    await user.selectOptions(within(websiteCard!).getByLabelText("Permission"), "allow_session");
    expect(updateTool).toHaveBeenCalledWith("open_website", {
      permission: "allow_session",
    });
  });

  it("prevents permanent approval for high-risk tools and explains the guardrail", () => {
    render(<ToolsPage />);

    const deleteCard = screen.getByRole("heading", { name: "delete path" }).closest("article");
    expect(deleteCard).not.toBeNull();
    const permanent = within(deleteCard!).getByRole("option", {
      name: /always allow — unavailable for high risk/i,
    });

    expect(permanent).toBeDisabled();
    expect(within(deleteCard!).getByText("Fresh confirmation is always required.")).toBeVisible();
  });

  it("filters tools by risk and search text", async () => {
    const user = userEvent.setup();
    render(<ToolsPage />);

    await user.click(screen.getByRole("button", { name: "high" }));
    expect(screen.getByRole("heading", { name: "delete path" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "open website" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "all" }));
    await user.type(screen.getByRole("searchbox", { name: "Search registered tools" }), "browser");
    expect(screen.getByRole("heading", { name: "open website" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "delete path" })).not.toBeInTheDocument();
  });
});
