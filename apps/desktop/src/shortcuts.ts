import type { ShortcutHandler } from "@tauri-apps/plugin-global-shortcut";

type RegisterShortcuts = (shortcuts: string | string[], handler: ShortcutHandler) => Promise<void>;
type UnregisterShortcuts = (shortcuts: string | string[]) => Promise<void>;
interface ActiveRegistration {
  shortcuts: string[];
  handler: ShortcutHandler;
}

const modifierAliases = new Map<string, string>([
  ["alt", "alt"],
  ["cmdorctrl", "ctrl"],
  ["commandorcontrol", "ctrl"],
  ["control", "ctrl"],
  ["ctrl", "ctrl"],
  ["meta", "super"],
  ["shift", "shift"],
  ["super", "super"],
  ["win", "super"],
  ["windows", "super"],
]);

const namedKeys = new Set([
  "arrowdown",
  "arrowleft",
  "arrowright",
  "arrowup",
  "backspace",
  "capslock",
  "delete",
  "down",
  "end",
  "enter",
  "esc",
  "escape",
  "home",
  "insert",
  "left",
  "numlock",
  "pagedown",
  "pageup",
  "pause",
  "printscreen",
  "return",
  "right",
  "scrolllock",
  "space",
  "tab",
  "up",
]);

export function shortcutValidationError(value: string): string | null {
  const parts = value.split("+").map((part) => part.trim());
  if (!value.trim()) return "Enter a shortcut with a modifier and a key.";
  if (parts.length < 2 || parts.some((part) => !part)) {
    return "Use a complete shortcut such as Ctrl+Shift+J.";
  }

  const key = parts.at(-1)?.toLowerCase() ?? "";
  const modifiers = parts.slice(0, -1).map((part) => modifierAliases.get(part.toLowerCase()));
  if (modifiers.some((modifier) => modifier === undefined)) {
    return "Put one or more supported modifiers before the final key.";
  }
  if (new Set(modifiers).size !== modifiers.length) return "Do not repeat shortcut modifiers.";
  if (modifierAliases.has(key)) return "Add a non-modifier key to the shortcut.";

  const functionKey = /^f(?:[1-9]|1[0-9]|2[0-4])$/.test(key);
  if (!/^[a-z0-9]$/.test(key) && !functionKey && !namedKeys.has(key)) {
    return "Use a letter, number, function key, or supported named key.";
  }
  return null;
}

export function normalizeShortcut(value: string): string {
  return value
    .split("+")
    .map((part) => modifierAliases.get(part.trim().toLowerCase()) ?? part.trim().toLowerCase())
    .join("+");
}

export class ShortcutRegistrationCoordinator {
  private active: ActiveRegistration | null = null;
  private generation = 0;
  private tail: Promise<void> = Promise.resolve();

  constructor(
    private readonly registerShortcuts: RegisterShortcuts,
    private readonly unregisterShortcuts: UnregisterShortcuts,
  ) {}

  replace(
    shortcuts: string[],
    handler: ShortcutHandler,
    onError: (error: unknown) => void,
    onRegistered: () => void = () => undefined,
  ): () => void {
    const generation = ++this.generation;
    const requested = [...shortcuts];
    this.enqueue(
      generation,
      async () => {
        const previous = await this.unregisterActive();
        if (requested.length === 0) return;
        try {
          await this.registerShortcuts(requested, handler);
          // A newer request may arrive while registration is pending. Keep ownership here so
          // that the serialized newer request removes exactly these keys before replacing them.
          this.active = { shortcuts: requested, handler };
          if (generation === this.generation) onRegistered();
        } catch (error) {
          if (previous) {
            try {
              await this.registerShortcuts(previous.shortcuts, previous.handler);
              this.active = previous;
            } catch (rollbackError) {
              throw new AggregateError(
                [error, rollbackError],
                "The new shortcut and rollback registration both failed",
              );
            }
          }
          throw error;
        }
      },
      onError,
    );

    return () => {
      if (generation !== this.generation) return;
      const cleanupGeneration = ++this.generation;
      this.enqueue(
        cleanupGeneration,
        async () => {
          await this.unregisterActive();
        },
        onError,
      );
    };
  }

  async settled(): Promise<void> {
    await this.tail;
  }

  private enqueue(
    generation: number,
    operation: () => Promise<void>,
    onError: (error: unknown) => void,
  ): void {
    const scheduled = this.tail.then(async () => {
      if (generation !== this.generation) return;
      await operation();
    });
    this.tail = scheduled.catch((error: unknown) => {
      if (generation === this.generation) onError(error);
    });
  }

  private async unregisterActive(): Promise<ActiveRegistration | null> {
    if (!this.active) return null;
    const registered = this.active;
    await this.unregisterShortcuts(registered.shortcuts);
    this.active = null;
    return registered;
  }
}
