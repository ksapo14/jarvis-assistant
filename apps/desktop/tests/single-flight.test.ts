import { describe, expect, it, vi } from "vitest";
import { SingleFlight } from "@/single-flight";

describe("SingleFlight", () => {
  it("ignores re-entry until the active operation settles", async () => {
    let release: (() => void) | undefined;
    const task = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const flight = new SingleFlight();

    const first = flight.run(task);
    const duplicate = flight.run(task);

    expect(flight.isActive).toBe(true);
    expect(await duplicate).toBe(false);
    expect(task).toHaveBeenCalledOnce();

    release?.();
    expect(await first).toBe(true);
    expect(flight.isActive).toBe(false);

    expect(await flight.run(() => Promise.resolve())).toBe(true);
  });

  it("unlocks after a failed operation", async () => {
    const flight = new SingleFlight();

    await expect(flight.run(() => Promise.reject(new Error("request failed")))).rejects.toThrow(
      "request failed",
    );

    expect(flight.isActive).toBe(false);
    expect(await flight.run(() => Promise.resolve())).toBe(true);
  });
});
