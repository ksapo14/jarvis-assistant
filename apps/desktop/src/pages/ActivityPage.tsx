import { useEffect } from "react";
import { Icon } from "@/components/Icon";
import { useAssistant } from "@/state/context";

const formatDate = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));

export function ActivityPage() {
  const { history, refreshHistory } = useAssistant();
  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  return (
    <div className="standard-page">
      <header className="page-header split-header">
        <div>
          <div className="eyebrow">Local audit trail</div>
          <h1>Activity</h1>
          <p>Requests, approvals, and redacted tool results stored on this device.</p>
        </div>
        <button className="button secondary" onClick={() => void refreshHistory()}>
          <Icon name="refresh" /> Refresh
        </button>
      </header>
      {history.length === 0 ? (
        <section className="empty-state">
          <Icon name="history" />
          <h2>No activity yet</h2>
          <p>Completed requests will appear here. Raw microphone audio is never retained.</p>
        </section>
      ) : (
        <div className="activity-list">
          {history.map((item) => (
            <article className="activity-item" key={item.id}>
              <div className="activity-time">
                <span className={`result-dot result-${item.status}`} />
                <time dateTime={item.createdAt}>{formatDate(item.createdAt)}</time>
              </div>
              <div className="activity-body">
                <h2>{item.userRequest}</h2>
                <p>{item.assistantResponse || "No spoken response recorded."}</p>
                <div className="activity-meta">
                  {item.toolName && <code>{item.toolName}</code>}
                  {item.riskLevel && (
                    <span className={`risk risk-${item.riskLevel}`}>{item.riskLevel}</span>
                  )}
                  {item.confirmationResult && <span>Confirmation: {item.confirmationResult}</span>}
                  <span>{item.status}</span>
                </div>
                {(item.toolArguments || item.toolResult) && (
                  <details className="activity-details">
                    <summary>Redacted tool details</summary>
                    {item.toolArguments && (
                      <div>
                        <strong>Arguments</strong>
                        <pre>{JSON.stringify(item.toolArguments, null, 2)}</pre>
                      </div>
                    )}
                    {item.toolResult && (
                      <div>
                        <strong>Result</strong>
                        <pre>{JSON.stringify(item.toolResult, null, 2)}</pre>
                      </div>
                    )}
                  </details>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
