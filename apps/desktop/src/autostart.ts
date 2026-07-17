export interface AutostartAdapter {
  isEnabled: () => Promise<boolean>;
  enable: () => Promise<void>;
  disable: () => Promise<void>;
}

export interface AutostartReconcileResult {
  enabled: boolean;
  warning?: Error;
}

function asError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

function combinedError(message: string, errors: unknown[]): Error {
  const details = errors.map((error) => asError(error).message).join("; ");
  return new Error(`${message}: ${details}`);
}

/**
 * Serializes changes to the OS login registration and keeps them transactional
 * with the backend's persisted setting.
 */
export class AutostartCoordinator {
  private tail: Promise<void> = Promise.resolve();

  constructor(private readonly adapter: AutostartAdapter) {}

  update<T>(desired: boolean, persist: () => Promise<T>): Promise<T> {
    return this.serialized(async () => {
      const previous = await this.adapter.isEnabled();
      await this.apply(desired, previous);
      try {
        return await persist();
      } catch (persistError) {
        try {
          await this.apply(previous, desired);
        } catch (rollbackError) {
          throw combinedError("Saving startup failed and the OS registration rollback failed", [
            persistError,
            rollbackError,
          ]);
        }
        throw persistError;
      }
    });
  }

  clear(clearBackend: () => Promise<void>): Promise<void> {
    return this.serialized(async () => {
      const previous = await this.adapter.isEnabled();
      await this.apply(false, previous);
      try {
        await clearBackend();
      } catch (clearError) {
        try {
          await this.apply(previous, false);
        } catch (rollbackError) {
          throw combinedError("Clearing data failed and the OS registration rollback failed", [
            clearError,
            rollbackError,
          ]);
        }
        throw clearError;
      }
    });
  }

  /**
   * Makes the persisted backend preference authoritative at startup. If the OS
   * plugin cannot apply it, the backend is changed to the effective OS state so
   * the two sources do not quietly disagree.
   */
  reconcile(
    desired: boolean,
    reflectEffectiveState: (enabled: boolean) => Promise<void>,
  ): Promise<AutostartReconcileResult> {
    return this.serialized(async () => {
      let actual: boolean;
      try {
        actual = await this.adapter.isEnabled();
      } catch (error) {
        return {
          enabled: desired,
          warning: combinedError("Could not read the Windows startup registration", [error]),
        };
      }

      if (actual === desired) return { enabled: actual };

      try {
        await this.apply(desired, actual);
        return { enabled: desired };
      } catch (applyError) {
        const errors: unknown[] = [applyError];
        let effective = actual;
        try {
          effective = await this.adapter.isEnabled();
        } catch (readError) {
          errors.push(readError);
        }
        try {
          await reflectEffectiveState(effective);
        } catch (persistError) {
          errors.push(persistError);
        }
        return {
          enabled: effective,
          warning: combinedError("Could not reconcile the Windows startup registration", errors),
        };
      }
    });
  }

  private async apply(desired: boolean, knownCurrent?: boolean): Promise<void> {
    if (knownCurrent === desired) return;
    if (desired) await this.adapter.enable();
    else await this.adapter.disable();
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.tail.then(operation, operation);
    this.tail = next.then(
      () => undefined,
      () => undefined,
    );
    return next;
  }
}
