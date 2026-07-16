import { invoke } from "@tauri-apps/api/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistantApi } from "@/api/client";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

describe("AssistantApi Tauri bootstrap", () => {
  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("uses the authenticated loopback URL supplied by the desktop host", async () => {
    window.__TAURI_INTERNALS__ = {};
    vi.mocked(invoke).mockResolvedValue({
      sessionToken: "host-generated-session-token",
      baseUrl: "http://127.0.0.1:9321/",
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ state: "idle" }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const api = new AssistantApi();

    await api.initialize();
    await api.state();

    expect(invoke).toHaveBeenCalledWith("get_backend_config");
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://127.0.0.1:9321/v1/state");
    expect(new Headers(init?.headers).get("X-Assistant-Token")).toBe(
      "host-generated-session-token",
    );
    expect(api.websocketUrl()).toBe("ws://127.0.0.1:9321/v1/events");
  });
});
