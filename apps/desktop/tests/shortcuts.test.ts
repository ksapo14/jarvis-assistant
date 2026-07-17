import { describe, expect, it, vi } from "vitest";
import { ShortcutRegistrationCoordinator, shortcutValidationError } from "@/shortcuts";

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

describe("shortcut safety", () => {
  it.each(["", " ", "Ctrl", "Ctrl+", "Ctrl+Shift", "Ctrl+Ctrl+J", "Ctrl+UnknownKey"])(
    "rejects the incomplete shortcut %j",
    (shortcut) => expect(shortcutValidationError(shortcut)).not.toBeNull(),
  );

  it.each(["Ctrl+Space", "Ctrl+Shift+J", "CmdOrCtrl+F12", "Alt+Enter"])(
    "accepts the complete shortcut %j",
    (shortcut) => expect(shortcutValidationError(shortcut)).toBeNull(),
  );

  it("serializes a stale registration before replacing shared keys", async () => {
    const firstRegistration = deferred();
    const order: string[] = [];
    const register = vi.fn(async (shortcuts: string | string[]) => {
      const values = typeof shortcuts === "string" ? [shortcuts] : shortcuts;
      order.push(`register:${values.join(",")}`);
      if (register.mock.calls.length === 1) await firstRegistration.promise;
    });
    const unregister = vi.fn((shortcuts: string | string[]) => {
      const values = typeof shortcuts === "string" ? [shortcuts] : shortcuts;
      order.push(`unregister:${values.join(",")}`);
      return Promise.resolve();
    });
    const reportError = vi.fn();
    const coordinator = new ShortcutRegistrationCoordinator(register, unregister);
    const oldShortcuts = ["Ctrl+Shift+J", "Ctrl+Space"];
    const newShortcuts = ["Ctrl+Alt+J", "Ctrl+Space"];

    const disposeOld = coordinator.replace(oldShortcuts, vi.fn(), reportError);
    await vi.waitFor(() => expect(register).toHaveBeenCalledTimes(1));
    disposeOld();
    coordinator.replace(newShortcuts, vi.fn(), reportError);
    firstRegistration.resolve();
    await coordinator.settled();

    expect(order).toEqual([
      `register:${oldShortcuts.join(",")}`,
      `unregister:${oldShortcuts.join(",")}`,
      `register:${newShortcuts.join(",")}`,
    ]);
    expect(reportError).not.toHaveBeenCalled();
  });

  it("restores the previous shortcuts when a replacement cannot register", async () => {
    const order: string[] = [];
    const oldShortcuts = ["Ctrl+Shift+J", "Ctrl+Space"];
    const newShortcuts = ["Ctrl+Alt+J", "Ctrl+Space"];
    const register = vi.fn((shortcuts: string | string[]) => {
      const values = typeof shortcuts === "string" ? [shortcuts] : shortcuts;
      order.push(`register:${values.join(",")}`);
      return values[0] === "Ctrl+Alt+J"
        ? Promise.reject(new Error("shortcut collision"))
        : Promise.resolve();
    });
    const unregister = vi.fn((shortcuts: string | string[]) => {
      const values = typeof shortcuts === "string" ? [shortcuts] : shortcuts;
      order.push(`unregister:${values.join(",")}`);
      return Promise.resolve();
    });
    const reportError = vi.fn();
    const registered = vi.fn();
    const coordinator = new ShortcutRegistrationCoordinator(register, unregister);
    const oldHandler = vi.fn();

    const disposeOld = coordinator.replace(oldShortcuts, oldHandler, reportError, registered);
    await coordinator.settled();
    disposeOld();
    const disposeNew = coordinator.replace(newShortcuts, vi.fn(), reportError);
    await coordinator.settled();

    expect(order).toEqual([
      `register:${oldShortcuts.join(",")}`,
      `unregister:${oldShortcuts.join(",")}`,
      `register:${newShortcuts.join(",")}`,
      `register:${oldShortcuts.join(",")}`,
    ]);
    expect(reportError).toHaveBeenCalledWith(
      expect.objectContaining({ message: "shortcut collision" }),
    );
    expect(registered).toHaveBeenCalledTimes(1);

    disposeNew();
    await coordinator.settled();
    expect(order.at(-1)).toBe(`unregister:${oldShortcuts.join(",")}`);
  });
});
