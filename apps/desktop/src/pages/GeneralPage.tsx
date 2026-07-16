import { invoke } from "@tauri-apps/api/core";
import { useState } from "react";
import { DraftTextInput } from "@/components/DraftTextInput";
import { Toggle } from "@/components/Toggle";
import { shortcutValidationError } from "@/shortcuts";
import { useAssistant } from "@/state/context";

export function GeneralPage() {
  const { settings, updateSettings, clearData, reportError } = useAssistant();
  const [confirmClear, setConfirmClear] = useState(false);
  return (
    <div className="standard-page">
      <header className="page-header">
        <div className="eyebrow">Application behavior</div>
        <h1>General settings</h1>
        <p>Startup, privacy, keyboard access, and developer safeguards.</p>
      </header>
      <section className="settings-card">
        <div className="setting-row">
          <div>
            <strong>Launch on startup</strong>
            <span>Start quietly when you sign in to Windows.</span>
          </div>
          <Toggle
            checked={settings.launchOnStartup}
            label="Launch on startup"
            onChange={(launchOnStartup) => void updateSettings({ launchOnStartup })}
          />
        </div>
        <div className="setting-row">
          <div>
            <strong>Minimize to tray</strong>
            <span>Closing the window keeps the assistant available in the notification area.</span>
          </div>
          <Toggle
            checked={settings.minimizeToTray}
            label="Minimize to tray"
            onChange={(minimizeToTray) => void updateSettings({ minimizeToTray })}
          />
        </div>
        <div className="setting-row">
          <div>
            <strong>Activation sound</strong>
            <span>Play a short local cue after the wake word is recognized.</span>
          </div>
          <Toggle
            checked={settings.playActivationSound}
            label="Activation sound"
            onChange={(playActivationSound) => void updateSettings({ playActivationSound })}
          />
        </div>
        <div className="setting-row">
          <div>
            <strong>Save conversation history</strong>
            <span>Store redacted transcripts and action summaries in local SQLite.</span>
          </div>
          <Toggle
            checked={settings.saveConversationHistory}
            label="Save conversation history"
            onChange={(saveConversationHistory) => void updateSettings({ saveConversationHistory })}
          />
        </div>
        <label className="field-row">
          <span>
            <strong>Open assistant shortcut</strong>
            <small>Registered globally by the Tauri host.</small>
          </span>
          <DraftTextInput
            value={settings.globalShortcut}
            validate={shortcutValidationError}
            onCommit={(globalShortcut) => updateSettings({ globalShortcut })}
            onValidationError={(message) =>
              reportError(`Open assistant shortcut was not saved: ${message}`)
            }
          />
        </label>
        <div className="setting-row developer-setting">
          <div>
            <strong>Developer mode</strong>
            <span>
              Enables configured commands only. Commands remain allowlisted, path-confined,
              previewed, confirmed, timed out, and output-limited.
            </span>
          </div>
          <Toggle
            checked={settings.developerMode}
            label="Developer mode"
            onChange={(developerMode) => void updateSettings({ developerMode })}
          />
        </div>
        <div className="setting-row">
          <div>
            <strong>Local diagnostic logs</strong>
            <span>Open the app-owned folder containing rotated, redacted JSON logs.</span>
          </div>
          <button
            className="button secondary"
            onClick={() => {
              if (!window.__TAURI_INTERNALS__) {
                reportError("View logs is available in the Tauri desktop application.");
                return;
              }
              void invoke("open_log_directory").catch((error: unknown) =>
                reportError(String(error)),
              );
            }}
          >
            View logs
          </button>
        </div>
      </section>
      <section className="danger-zone">
        <div>
          <h2>Clear local data</h2>
          <p>
            Deletes settings, summaries, recent commands, permissions, action history, rotated logs,
            and app-owned screenshots. Startup registration is disabled.
          </p>
        </div>
        {!confirmClear ? (
          <button className="button secondary" onClick={() => setConfirmClear(true)}>
            Clear data…
          </button>
        ) : (
          <div className="inline-confirm">
            <span>API keys and external voice/wake-model files are not affected. Continue?</span>
            <button className="button ghost" onClick={() => setConfirmClear(false)}>
              Cancel
            </button>
            <button
              className="button danger"
              onClick={() => {
                setConfirmClear(false);
                void clearData();
              }}
            >
              Delete local data
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
