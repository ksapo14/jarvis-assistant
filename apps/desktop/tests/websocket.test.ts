import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EventSocket } from "@/api/websocket";
import type { ServerEvent } from "@/types";

type SocketEventName = "open" | "message" | "close" | "error";
type SocketListener = (event: Event | MessageEvent) => void;

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readonly sent: string[] = [];
  readonly listeners = new Map<SocketEventName, SocketListener[]>();
  readonly url: string;
  readonly close = vi.fn();

  constructor(url: string | URL) {
    this.url = String(url);
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: SocketEventName, listener: EventListenerOrEventListenerObject): void {
    const callback: SocketListener =
      typeof listener === "function" ? listener : (event) => listener.handleEvent(event);
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(callback);
    this.listeners.set(type, listeners);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  emit(type: SocketEventName, event: Event | MessageEvent = new Event(type)): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

describe("EventSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("authenticates on open and emits normalized backend events", () => {
    const onEvent = vi.fn<(event: ServerEvent) => void>();
    const onConnectionChange = vi.fn<(connected: boolean) => void>();
    const socket = new EventSocket({
      url: "ws://127.0.0.1:8765/v1/events",
      token: "one-time-session-token",
      onEvent,
      onConnectionChange,
    });

    socket.connect();
    const transport = MockWebSocket.instances[0];
    expect(transport).toBeDefined();
    transport!.emit("open");
    transport!.emit(
      "message",
      new MessageEvent("message", {
        data: JSON.stringify({
          type: "status_changed",
          id: "00000000-0000-4000-8000-000000000004",
          timestamp: "2026-07-16T12:00:00.000Z",
          payload: {
            state: "listening",
            detail: "Listening for a command",
          },
        }),
      }),
    );

    expect(transport!.sent).toEqual([
      JSON.stringify({ type: "authenticate", token: "one-time-session-token" }),
    ]);
    expect(onConnectionChange).toHaveBeenCalledWith(true);
    expect(onEvent).toHaveBeenCalledOnce();
    const [event] = onEvent.mock.calls[0]!;
    expect(event.type).toBe("status_changed");
    if (event.type !== "status_changed") throw new Error("Expected a status event");
    expect(event.payload).toMatchObject({
      state: "listening",
      detail: "Listening for a command",
      timestamp: "2026-07-16T12:00:00.000Z",
    });
  });

  it("converts malformed JSON into a recoverable UI error", () => {
    const onEvent = vi.fn<(event: ServerEvent) => void>();
    const socket = new EventSocket({
      url: "ws://127.0.0.1:8765/v1/events",
      token: "token",
      onEvent,
      onConnectionChange: vi.fn(),
    });

    socket.connect();
    MockWebSocket.instances[0]!.emit("message", new MessageEvent("message", { data: "not-json" }));

    expect(onEvent).toHaveBeenCalledWith({
      type: "error",
      payload: {
        code: "invalid_backend_event",
        message: "The backend sent an invalid event.",
        recoverable: true,
      },
    });
  });

  it("reconnects with backoff after an unexpected close but not after disconnect", () => {
    const onConnectionChange = vi.fn<(connected: boolean) => void>();
    const socket = new EventSocket({
      url: "ws://127.0.0.1:8765/v1/events",
      token: "token",
      onEvent: vi.fn(),
      onConnectionChange,
    });

    socket.connect();
    MockWebSocket.instances[0]!.emit("close");
    expect(onConnectionChange).toHaveBeenCalledWith(false);

    vi.advanceTimersByTime(499);
    expect(MockWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(2);

    const current = MockWebSocket.instances[1]!;
    socket.disconnect();
    current.emit("close");
    vi.advanceTimersByTime(20_000);
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it("refreshes the host connection before reconnecting to a restarted backend", async () => {
    const resolveConnection = vi
      .fn<() => Promise<{ url: string; token: string }>>()
      .mockResolvedValueOnce({ url: "ws://127.0.0.1:9101/v1/events", token: "token-one" })
      .mockResolvedValueOnce({ url: "ws://127.0.0.1:9102/v1/events", token: "token-two" });
    const socket = new EventSocket({
      url: "ws://127.0.0.1:8765/v1/events",
      token: "stale-token",
      resolveConnection,
      onEvent: vi.fn(),
      onConnectionChange: vi.fn(),
    });

    socket.connect();
    await Promise.resolve();
    expect(MockWebSocket.instances[0]!.url).toBe("ws://127.0.0.1:9101/v1/events");
    MockWebSocket.instances[0]!.emit("open");
    expect(MockWebSocket.instances[0]!.sent).toEqual([
      JSON.stringify({ type: "authenticate", token: "token-one" }),
    ]);

    MockWebSocket.instances[0]!.emit("close");
    await vi.advanceTimersByTimeAsync(500);
    expect(resolveConnection).toHaveBeenCalledTimes(2);
    expect(MockWebSocket.instances[1]!.url).toBe("ws://127.0.0.1:9102/v1/events");
    MockWebSocket.instances[1]!.emit("open");
    expect(MockWebSocket.instances[1]!.sent).toEqual([
      JSON.stringify({ type: "authenticate", token: "token-two" }),
    ]);
  });
});
