import type { ServerEvent } from "@/types";
import { normalizeEvent } from "./normalizers";

interface SocketOptions {
  url: string;
  token: string;
  resolveConnection?: () => Promise<{ url: string; token: string }>;
  onEvent: (event: ServerEvent) => void;
  onConnectionChange: (connected: boolean) => void;
}

export class EventSocket {
  private socket: WebSocket | null = null;
  private retryTimer: number | null = null;
  private attempts = 0;
  private closedByClient = false;
  private generation = 0;

  constructor(private readonly options: SocketOptions) {}

  connect(): void {
    this.closedByClient = false;
    const generation = ++this.generation;
    void this.open(generation);
  }

  private async open(generation: number): Promise<void> {
    let url = this.options.url;
    let token = this.options.token;
    try {
      if (this.options.resolveConnection) {
        const resolved = await this.options.resolveConnection();
        url = resolved.url;
        token = resolved.token;
      }
    } catch {
      if (generation !== this.generation || this.closedByClient) return;
      this.options.onConnectionChange(false);
      this.scheduleReconnect();
      return;
    }
    if (generation !== this.generation || this.closedByClient) return;

    const socket = new WebSocket(url);
    this.socket = socket;
    socket.addEventListener("open", () => {
      if (socket !== this.socket) return;
      this.attempts = 0;
      socket.send(JSON.stringify({ type: "authenticate", token }));
      this.options.onConnectionChange(true);
    });
    socket.addEventListener("message", (message) => {
      if (socket !== this.socket) return;
      try {
        const event = normalizeEvent(JSON.parse(String(message.data)));
        if (event) this.options.onEvent(event);
        else this.emitInvalidEvent();
      } catch {
        this.emitInvalidEvent();
      }
    });
    socket.addEventListener("close", () => {
      if (socket !== this.socket) return;
      this.socket = null;
      this.options.onConnectionChange(false);
      this.scheduleReconnect();
    });
    socket.addEventListener("error", () => {
      if (socket === this.socket) this.options.onConnectionChange(false);
    });
  }

  disconnect(): void {
    this.closedByClient = true;
    this.generation += 1;
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
    this.socket?.close(1000, "Desktop window closed");
    this.socket = null;
  }

  private scheduleReconnect(): void {
    if (this.closedByClient || this.retryTimer !== null) return;
    const backoff = Math.min(15_000, 500 * 2 ** this.attempts);
    const jitter = Math.floor(Math.random() * 250);
    this.attempts += 1;
    this.retryTimer = window.setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, backoff + jitter);
  }

  private emitInvalidEvent(): void {
    this.options.onEvent({
      type: "error",
      payload: {
        code: "invalid_backend_event",
        message: "The backend sent an invalid event.",
        recoverable: true,
      },
    });
  }
}
