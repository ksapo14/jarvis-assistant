import { useEffect, useState } from "react";
import type { ConfirmationRequest } from "@/types";
import { Icon } from "./Icon";

interface ConfirmationCardProps {
  request: ConfirmationRequest;
  onDecision: (approved: boolean) => void;
}

export function ConfirmationCard({ request, onDecision }: ConfirmationCardProps) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [request.id, request.expiresAt]);
  const expiresIn = Math.max(0, Math.ceil((new Date(request.expiresAt).getTime() - now) / 1000));
  const expired = expiresIn === 0;
  return (
    <section
      className="confirmation-card"
      aria-labelledby="confirmation-title"
      aria-live="assertive"
    >
      <div className="confirmation-icon">
        <Icon name="shield" />
      </div>
      <div className="confirmation-copy">
        <div className="eyebrow">{request.riskLevel} risk · approval required</div>
        <h2 id="confirmation-title">Review before JARVIS acts</h2>
        <p>{request.prompt}</p>
        {request.actionPreview && request.actionPreview !== request.prompt && (
          <code>{request.actionPreview}</code>
        )}
        <details className="confirmation-arguments" open>
          <summary>Exact tool arguments</summary>
          <pre aria-label="Exact tool arguments">{JSON.stringify(request.arguments, null, 2)}</pre>
        </details>
        <small>
          {expired
            ? "This request expired. JARVIS will not execute it."
            : `This request expires in about ${expiresIn} seconds. A changed action needs fresh approval.`}
        </small>
      </div>
      <div className="confirmation-actions">
        <button className="button secondary" onClick={() => onDecision(false)} disabled={expired}>
          <Icon name="x" /> Deny
        </button>
        <button className="button danger" onClick={() => onDecision(true)} disabled={expired}>
          <Icon name="check" /> Approve exact action
        </button>
      </div>
    </section>
  );
}
