import { DraftTextInput } from "@/components/DraftTextInput";
import { Toggle } from "@/components/Toggle";
import { shortcutValidationError } from "@/shortcuts";
import { useAssistant } from "@/state/context";

export function VoicePage() {
  const { settings, microphones, updateSettings, reportError } = useAssistant();
  return (
    <div className="standard-page">
      <header className="page-header">
        <div className="eyebrow">Local-first audio</div>
        <h1>Voice</h1>
        <p>
          The microphone stays local until wake detection or an intentional push-to-talk action.
        </p>
      </header>
      <section className="settings-section">
        <div className="section-heading">
          <h2>Listening</h2>
          <p>Wake detection pauses automatically while JARVIS is speaking.</p>
        </div>
        <div className="settings-card">
          <div className="setting-row">
            <div>
              <strong>Wake word</strong>
              <span>Use local openWakeWord detection in the background.</span>
            </div>
            <Toggle
              checked={settings.wakeWordEnabled}
              label="Toggle wake word"
              onChange={(wakeWordEnabled) => void updateSettings({ wakeWordEnabled })}
            />
          </div>
          <label className="field-row">
            <span>
              <strong>Wake phrase</strong>
              <small>
                Stock support is “Hey Jarvis”; bare “Jarvis” needs a custom compatible model.
              </small>
            </span>
            <DraftTextInput
              value={settings.wakePhrase}
              validate={(value) => (value.trim() ? null : "Wake phrase cannot be blank.")}
              onCommit={(wakePhrase) => updateSettings({ wakePhrase: wakePhrase.trim() })}
              onValidationError={(message) => reportError(`Wake phrase was not saved: ${message}`)}
              disabled={!settings.wakeWordEnabled}
            />
          </label>
          <label className="field-row range-row">
            <span>
              <strong>Sensitivity</strong>
              <small>
                Raise carefully if the phrase is missed; higher values may increase false
                activations.
              </small>
            </span>
            <div className="range-control">
              <input
                type="range"
                min="0.1"
                max="0.95"
                step="0.05"
                value={settings.wakeSensitivity}
                onChange={(event) =>
                  void updateSettings({ wakeSensitivity: Number(event.target.value) })
                }
                disabled={!settings.wakeWordEnabled}
              />
              <output>{Math.round(settings.wakeSensitivity * 100)}%</output>
            </div>
          </label>
          <label className="field-row">
            <span>
              <strong>Microphone</strong>
              <small>Only input-capable devices are listed.</small>
            </span>
            <select
              value={settings.microphoneDevice ?? ""}
              onChange={(event) =>
                void updateSettings({ microphoneDevice: event.target.value || null })
              }
            >
              <option value="">System default</option>
              {microphones.map((device) => (
                <option value={device.id} key={device.id}>
                  {device.name}
                  {device.isDefault ? " (default)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="field-row">
            <span>
              <strong>Push-to-talk shortcut</strong>
              <small>Available even when wake-word detection is disabled.</small>
            </span>
            <DraftTextInput
              value={settings.pushToTalkShortcut}
              validate={shortcutValidationError}
              onCommit={(pushToTalkShortcut) => updateSettings({ pushToTalkShortcut })}
              onValidationError={(message) =>
                reportError(`Push-to-talk shortcut was not saved: ${message}`)
              }
            />
          </label>
        </div>
      </section>
      <section className="settings-section">
        <div className="section-heading">
          <h2>Speech output</h2>
          <p>
            Piper renders speech locally. Technical details remain on screen instead of being read
            in full.
          </p>
        </div>
        <div className="settings-card">
          <label className="field-row">
            <span>
              <strong>Piper executable</strong>
              <small>Absolute path to piper.exe.</small>
            </span>
            <DraftTextInput
              value={settings.piperExecutablePath}
              onCommit={(piperExecutablePath) => updateSettings({ piperExecutablePath })}
              placeholder="C:\\Tools\\piper\\piper.exe"
            />
          </label>
          <label className="field-row">
            <span>
              <strong>Voice model</strong>
              <small>ONNX voice files are installed separately according to their licenses.</small>
            </span>
            <DraftTextInput
              value={settings.piperModelPath}
              onCommit={(piperModelPath) => updateSettings({ piperModelPath })}
              placeholder="C:\\Users\\you\\Voices\\en_US-lessac-medium.onnx"
            />
          </label>
          <label className="field-row range-row">
            <span>
              <strong>Speech rate</strong>
              <small>Adjust Piper's duration scale.</small>
            </span>
            <div className="range-control">
              <input
                type="range"
                min="0.5"
                max="2"
                step="0.05"
                value={settings.speechRate}
                onChange={(event) =>
                  void updateSettings({ speechRate: Number(event.target.value) })
                }
              />
              <output>{settings.speechRate.toFixed(2)}×</output>
            </div>
          </label>
          <label className="field-row range-row">
            <span>
              <strong>Speech volume</strong>
              <small>Applied to generated local playback.</small>
            </span>
            <div className="range-control">
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={settings.speechVolume}
                onChange={(event) =>
                  void updateSettings({ speechVolume: Number(event.target.value) })
                }
              />
              <output>{Math.round(settings.speechVolume * 100)}%</output>
            </div>
          </label>
          <div className="setting-row">
            <div>
              <strong>Mute voice responses</strong>
              <span>Keep full responses visible without audio playback.</span>
            </div>
            <Toggle
              checked={settings.voiceMuted}
              label="Mute voice responses"
              onChange={(voiceMuted) => void updateSettings({ voiceMuted })}
            />
          </div>
        </div>
      </section>
    </div>
  );
}
