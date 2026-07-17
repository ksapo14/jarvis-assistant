import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistantApi } from "@/api/client";
import type { ConfirmationRequest } from "@/types";

describe("AssistantApi confirmation decisions", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("submits the one-use token and unchanged-action fingerprint with approval", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      status: 204,
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const api = new AssistantApi("http://127.0.0.1:8765");
    await api.initialize();
    const request: ConfirmationRequest = {
      id: "confirmation/id",
      toolName: "delete_path",
      prompt: "Delete the selected file?",
      actionPreview: String.raw`Delete C:\Users\Ada\Downloads\old.zip`,
      arguments: { path: String.raw`C:\Users\Ada\Downloads\old.zip` },
      riskLevel: "high",
      expiresAt: "2026-07-16T12:01:00.000Z",
      confirmationToken: "one-use-secret-token",
      actionFingerprint: "sha256:fixed-action-digest",
    };

    await api.decideConfirmation(request, true);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://127.0.0.1:8765/v1/confirmations/confirmation%2Fid/decide");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(
      JSON.stringify({
        decision: "yes",
        confirmation_token: "one-use-secret-token",
        action_fingerprint: "sha256:fixed-action-digest",
      }),
    );
    expect(init?.headers).toMatchObject({
      "X-Assistant-Token": "dev-token-change-me",
    });
  });

  it("restores a still-valid pending confirmation after a UI reconnect", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve([
            {
              id: "confirmation-2",
              tool_call: {
                name: "delete_path",
                arguments: { path: String.raw`C:\Users\Ada\Downloads\old.zip` },
              },
              prompt: "Delete the selected archive?",
              risk_level: "high",
              expires_at: "2099-07-16T12:01:00.000Z",
              confirmation_token: "restored-one-use-token",
              action_fingerprint: "restored-action-digest",
            },
          ]),
      } as Response),
    );
    const api = new AssistantApi("http://127.0.0.1:8765");
    await api.initialize();

    const pending = await api.pendingConfirmations();

    expect(pending).toHaveLength(1);
    expect(pending[0]).toMatchObject({
      id: "confirmation-2",
      toolName: "delete_path",
      confirmationToken: "restored-one-use-token",
      actionFingerprint: "restored-action-digest",
    });
  });
});
