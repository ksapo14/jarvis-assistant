import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmationCard } from "@/components/ConfirmationCard";
import type { ConfirmationRequest } from "@/types";

const request: ConfirmationRequest = {
  id: "confirmation-1",
  toolName: "delete_path",
  prompt: "You asked me to delete the selected archive. Should I continue?",
  actionPreview: String.raw`Delete C:\Users\Ada\Downloads\old.zip`,
  arguments: { path: String.raw`C:\Users\Ada\Downloads\old.zip` },
  riskLevel: "high",
  expiresAt: "2026-07-16T12:01:00.000Z",
  confirmationToken: "one-use-secret-token",
  actionFingerprint: "sha256:fixed-action-digest",
};

describe("ConfirmationCard", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-16T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the exact action, risk, and expiration without hiding sensitive scope", () => {
    render(<ConfirmationCard request={request} onDecision={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Review before JARVIS acts" })).toBeVisible();
    expect(screen.getByText("high risk · approval required")).toBeVisible();
    expect(screen.getByText(request.prompt)).toBeVisible();
    expect(screen.getByText(request.actionPreview)).toBeVisible();
    expect(JSON.parse(screen.getByLabelText("Exact tool arguments").textContent ?? "{}")).toEqual(
      request.arguments,
    );
    expect(screen.getByText(/expires in about 60 seconds/i)).toBeVisible();
    expect(screen.queryByText(request.confirmationToken)).not.toBeInTheDocument();
    expect(screen.queryByText(request.actionFingerprint)).not.toBeInTheDocument();
  });

  it("maps the deny and exact-approval controls to unambiguous decisions", () => {
    const onDecision = vi.fn<(approved: boolean) => void>();
    render(<ConfirmationCard request={request} onDecision={onDecision} />);

    fireEvent.click(screen.getByRole("button", { name: /deny/i }));
    fireEvent.click(screen.getByRole("button", { name: /approve exact action/i }));

    expect(onDecision).toHaveBeenNthCalledWith(1, false);
    expect(onDecision).toHaveBeenNthCalledWith(2, true);
  });

  it("ticks to expiry and disables both stale decision controls", async () => {
    const onDecision = vi.fn<(approved: boolean) => void>();
    render(
      <ConfirmationCard
        request={{ ...request, expiresAt: "2026-07-16T12:00:01.000Z" }}
        onDecision={onDecision}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100);
    });

    expect(screen.getByText(/request expired/i)).toBeVisible();
    expect(screen.getByRole("button", { name: /deny/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /approve exact action/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /approve exact action/i }));
    expect(onDecision).not.toHaveBeenCalled();
  });
});
