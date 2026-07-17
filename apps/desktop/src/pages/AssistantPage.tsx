import { type FormEvent, useState } from "react";
import { ConfirmationCard } from "@/components/ConfirmationCard";
import { Icon } from "@/components/Icon";
import { StatusBadge, stateLabel } from "@/components/StatusBadge";
import { useAssistant } from "@/state/context";

const activeStates = new Set([
  "wake_word_detected",
  "listening",
  "transcribing",
  "thinking",
  "waiting_for_confirmation",
  "executing",
  "speaking",
]);

export function AssistantPage() {
  const {
    snapshot,
    confirmation,
    settings,
    controlPending,
    toggleListening,
    cancel,
    sendCommand,
    decideConfirmation,
  } = useAssistant();
  const [command, setCommand] = useState("");
  const isListening = ["listening", "transcribing"].includes(snapshot.state);
  const isBusy = activeStates.has(snapshot.state);
  const canInterruptSpeech = snapshot.state === "speaking";
  const isControlPending = controlPending !== null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = command.trim();
    if (!text) return;
    setCommand("");
    void sendCommand(text);
  };

  return (
    <div className="assistant-page">
      <header className="page-header assistant-header">
        <div>
          <div className="eyebrow">Voice command center</div>
          <h1>Good to have you here.</h1>
          <p>{snapshot.detail}</p>
        </div>
        <StatusBadge state={snapshot.state} />
      </header>

      {confirmation && (
        <ConfirmationCard
          request={confirmation}
          onDecision={(approved) => void decideConfirmation(approved)}
        />
      )}

      <section className="command-stage" aria-label="Voice assistant controls">
        <div className={`voice-orbit state-${snapshot.state}`}>
          <span className="orbit-ring ring-one" />
          <span className="orbit-ring ring-two" />
          <button
            className="listen-button"
            onClick={() => void toggleListening()}
            disabled={
              !snapshot.connected ||
              isControlPending ||
              (isBusy && !isListening && !canInterruptSpeech)
            }
            aria-label={
              controlPending === "starting"
                ? "Starting listening"
                : controlPending === "stopping"
                  ? "Stopping listening"
                  : controlPending === "cancelling"
                    ? "Cancelling operation"
                    : isListening
                      ? "Stop listening"
                      : canInterruptSpeech
                        ? "Stop speaking"
                        : "Start push to talk"
            }
          >
            <Icon name={isListening || isControlPending ? "stop" : "microphone"} />
          </button>
        </div>
        <div className="stage-copy">
          <strong>{stateLabel(snapshot.state)}</strong>
          <span>
            {isControlPending
              ? controlPending === "starting"
                ? "Preparing the microphone. Please wait."
                : "Stopping the current operation. Please wait."
              : isListening
                ? "Speak naturally. Transcription starts only after activation."
                : settings.wakeWordEnabled
                  ? `Say “${settings.wakePhrase}” or press ${settings.pushToTalkShortcut} to begin.`
                  : `Wake word is paused. Press ${settings.pushToTalkShortcut} to begin.`}
          </span>
        </div>
        {isBusy && (
          <button
            className="button ghost cancel-button"
            onClick={() => void cancel()}
            disabled={isControlPending}
          >
            <Icon name="x" /> {isControlPending ? "Stopping…" : "Cancel operation"}
          </button>
        )}
      </section>

      <section className="conversation-grid">
        <article className="conversation-card transcript-card">
          <div className="card-heading">
            <span>User</span>
            {snapshot.microphoneActive && <i className="live-indicator">live</i>}
          </div>
          <p className={snapshot.liveTranscript || snapshot.finalTranscript ? "" : "placeholder"}>
            {snapshot.liveTranscript ||
              snapshot.finalTranscript ||
              "Your transcript will appear here."}
          </p>
        </article>
        <article className="conversation-card response-card">
          <div className="card-heading">
            <span>JARVIS</span>
            <Icon name="volume" />
          </div>
          <p className={snapshot.response ? "" : "placeholder"}>
            {snapshot.response || "Ready when you are."}
          </p>
        </article>
      </section>

      <form className="command-input" onSubmit={submit}>
        <label htmlFor="text-command" className="sr-only">
          Type a command
        </label>
        <input
          id="text-command"
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          placeholder="Type a command — useful in mock mode or quiet spaces"
          autoComplete="off"
          disabled={!snapshot.connected || isBusy || isControlPending}
        />
        <button
          className="button primary"
          type="submit"
          disabled={!command.trim() || isBusy || isControlPending}
        >
          Send
        </button>
      </form>

      <footer className="recent-action">
        <Icon name="activity" />
        <span>Recent action</span>
        <strong>{snapshot.recentAction || "No desktop actions this session"}</strong>
      </footer>
    </div>
  );
}
