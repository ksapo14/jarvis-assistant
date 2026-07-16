import { describe, expect, it, vi } from "vitest";
import { AutostartCoordinator, type AutostartAdapter } from "@/autostart";

function adapter(initial: boolean) {
  let enabled = initial;
  const calls: string[] = [];
  const value: AutostartAdapter = {
    isEnabled: vi.fn(() => {
      calls.push("read");
      return Promise.resolve(enabled);
    }),
    enable: vi.fn(() => {
      calls.push("enable");
      enabled = true;
      return Promise.resolve();
    }),
    disable: vi.fn(() => {
      calls.push("disable");
      enabled = false;
      return Promise.resolve();
    }),
  };
  return { value, calls, enabled: () => enabled };
}

describe("AutostartCoordinator", () => {
  it("applies the OS registration before persisting the desired setting", async () => {
    const fixture = adapter(false);
    const persist = vi.fn(() => {
      fixture.calls.push("persist");
      return Promise.resolve("saved");
    });

    await expect(new AutostartCoordinator(fixture.value).update(true, persist)).resolves.toBe(
      "saved",
    );
    expect(fixture.calls).toEqual(["read", "enable", "persist"]);
    expect(fixture.enabled()).toBe(true);
  });

  it("restores the previous OS registration when the backend PATCH fails", async () => {
    const fixture = adapter(false);
    const coordinator = new AutostartCoordinator(fixture.value);
    const persistError = new Error("backend rejected PATCH");

    await expect(
      coordinator.update(true, () => {
        fixture.calls.push("persist");
        return Promise.reject(persistError);
      }),
    ).rejects.toBe(persistError);
    expect(fixture.calls).toEqual(["read", "enable", "persist", "disable"]);
    expect(fixture.enabled()).toBe(false);
  });

  it("disables startup before clearing and restores it when backend clear fails", async () => {
    const fixture = adapter(true);
    const coordinator = new AutostartCoordinator(fixture.value);
    const clearError = new Error("clear failed");

    await expect(
      coordinator.clear(() => {
        fixture.calls.push("clear");
        return Promise.reject(clearError);
      }),
    ).rejects.toBe(clearError);
    expect(fixture.calls).toEqual(["read", "disable", "clear", "enable"]);
    expect(fixture.enabled()).toBe(true);
  });

  it("reconciles the OS registration to the backend preference at bootstrap", async () => {
    const fixture = adapter(false);
    const reflect = vi.fn(() => Promise.resolve());

    await expect(new AutostartCoordinator(fixture.value).reconcile(true, reflect)).resolves.toEqual(
      { enabled: true },
    );
    expect(fixture.calls).toEqual(["read", "enable"]);
    expect(reflect).not.toHaveBeenCalled();
  });

  it("reflects the effective OS state to the backend if registration fails", async () => {
    const fixture = adapter(false);
    vi.mocked(fixture.value.enable).mockImplementationOnce(() => {
      fixture.calls.push("enable-failed");
      return Promise.reject(new Error("Windows denied registration"));
    });
    const reflect = vi.fn((enabled: boolean) => {
      fixture.calls.push(`reflect-${enabled}`);
      return Promise.resolve();
    });

    const result = await new AutostartCoordinator(fixture.value).reconcile(true, reflect);

    expect(result.enabled).toBe(false);
    expect(result.warning?.message).toContain("Windows denied registration");
    expect(fixture.calls).toEqual(["read", "enable-failed", "read", "reflect-false"]);
    expect(reflect).toHaveBeenCalledWith(false);
  });

  it("serializes overlapping registration changes", async () => {
    const fixture = adapter(false);
    const coordinator = new AutostartCoordinator(fixture.value);
    let releaseFirst: (() => void) | undefined;
    const firstPersisting = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });

    const first = coordinator.update(true, async () => {
      fixture.calls.push("persist-first");
      await firstPersisting;
    });
    const second = coordinator.update(false, () => {
      fixture.calls.push("persist-second");
      return Promise.resolve();
    });
    await vi.waitFor(() => expect(fixture.calls).toContain("persist-first"));
    expect(fixture.calls).not.toContain("persist-second");
    releaseFirst?.();
    await Promise.all([first, second]);

    expect(fixture.calls).toEqual([
      "read",
      "enable",
      "persist-first",
      "read",
      "disable",
      "persist-second",
    ]);
  });
});
