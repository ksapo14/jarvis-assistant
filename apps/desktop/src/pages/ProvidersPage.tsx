import { useAssistant } from "@/state/context";

const fallbackProviders = [
  {
    name: "Deepgram" as const,
    status: "not_configured" as const,
    detail: "Set DEEPGRAM_API_KEY for post-activation streaming transcription.",
  },
  {
    name: "Gemini" as const,
    status: "not_configured" as const,
    detail: "Set GEMINI_API_KEY for reasoning and structured tool selection.",
  },
  {
    name: "Piper" as const,
    status: "not_configured" as const,
    detail: "Configure a Piper executable and separately licensed voice model.",
  },
  {
    name: "Wake word" as const,
    status: "not_configured" as const,
    detail: "Install the wake extra and choose an openWakeWord ONNX model.",
  },
];

export function ProvidersPage() {
  const { providers } = useAssistant();
  const visible = providers.length > 0 ? providers : fallbackProviders;
  return (
    <div className="standard-page">
      <header className="page-header">
        <div className="eyebrow">Replaceable provider adapters</div>
        <h1>Providers</h1>
        <p>Connection checks reveal configuration state without returning secret values.</p>
      </header>
      <div className="provider-grid">
        {visible.map((provider) => (
          <article className="provider-card" key={provider.name}>
            <div className="provider-topline">
              <span className={`provider-status ${provider.status}`} />
              <code>{provider.status.replaceAll("_", " ")}</code>
            </div>
            <h2>{provider.name}</h2>
            <p>{provider.detail}</p>
          </article>
        ))}
      </div>
      <section className="info-panel">
        <div>
          <h2>Environment-based secrets</h2>
          <p>
            Development reads <code>DEEPGRAM_API_KEY</code> and <code>GEMINI_API_KEY</code> from the
            process environment. API keys are never stored in SQLite or rendered here.
          </p>
        </div>
        <div>
          <h2>Offline development</h2>
          <p>
            Set <code>ASSISTANT_ENV=mock</code> to exercise transcription, tool selection,
            confirmation, and speech events without cloud credits or audio hardware.
          </p>
        </div>
        <div>
          <h2>Willow / WIS</h2>
          <p>
            A Willow WIS adapter is available for a user-operated local endpoint. It is not bundled
            and is distinct from the Windows wake-word pipeline.
          </p>
        </div>
      </section>
    </div>
  );
}
