import { useMemo, useState } from "react";
import { Toggle } from "@/components/Toggle";
import { useAssistant } from "@/state/context";
import type { PermissionLevel, RiskLevel } from "@/types";

const permissionLabels: Record<PermissionLevel, string> = {
  disabled: "Disabled",
  ask_every_time: "Ask every time",
  allow_session: "Allow this session",
  always_allow: "Always allow",
};

export function ToolsPage() {
  const { tools, updateTool } = useAssistant();
  const [query, setQuery] = useState("");
  const [risk, setRisk] = useState<RiskLevel | "all">("all");
  const visible = useMemo(
    () =>
      tools.filter(
        (tool) =>
          (risk === "all" || tool.riskLevel === risk) &&
          `${tool.name} ${tool.description} ${tool.permissionCategory}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [query, risk, tools],
  );

  return (
    <div className="standard-page">
      <header className="page-header">
        <div className="eyebrow">Explicit capability boundary</div>
        <h1>Tools & permissions</h1>
        <p>Gemini sees only enabled tools. Every argument is validated again before execution.</p>
      </header>
      <div className="toolbar">
        <input
          className="search-input"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search registered tools"
          aria-label="Search registered tools"
        />
        <div className="segmented" aria-label="Filter by risk">
          {(["all", "low", "medium", "high"] as const).map((value) => (
            <button
              key={value}
              className={risk === value ? "active" : ""}
              onClick={() => setRisk(value)}
            >
              {value}
            </button>
          ))}
        </div>
      </div>
      <div className="tool-list">
        {visible.map((tool) => {
          const permanentAllowed = tool.riskLevel !== "high";
          return (
            <article className={tool.enabled ? "tool-card" : "tool-card disabled"} key={tool.name}>
              <div className="tool-identity">
                <div>
                  <div className="tool-title-line">
                    <h2>{tool.name.replaceAll("_", " ")}</h2>
                    <span className={`risk risk-${tool.riskLevel}`}>{tool.riskLevel}</span>
                  </div>
                  <p>{tool.description}</p>
                  <small>
                    {tool.permissionCategory} · {tool.timeoutSeconds}s timeout
                    {tool.requiresConfirmation ? " · confirmation capable" : ""}
                  </small>
                </div>
                <Toggle
                  checked={tool.enabled}
                  label={`${tool.enabled ? "Disable" : "Enable"} ${tool.name}`}
                  onChange={(enabled) => void updateTool(tool.name, { enabled })}
                />
              </div>
              <div className="tool-permission">
                <label htmlFor={`permission-${tool.name}`}>Permission</label>
                <select
                  id={`permission-${tool.name}`}
                  value={tool.permission}
                  onChange={(event) =>
                    void updateTool(tool.name, {
                      permission: event.target.value as PermissionLevel,
                    })
                  }
                  disabled={!tool.enabled}
                >
                  {Object.entries(permissionLabels).map(([value, label]) => (
                    <option
                      key={value}
                      value={value}
                      disabled={value === "always_allow" && !permanentAllowed}
                    >
                      {label}
                      {value === "always_allow" && !permanentAllowed
                        ? " — unavailable for high risk"
                        : ""}
                    </option>
                  ))}
                </select>
                {tool.riskLevel === "high" && (
                  <span className="guardrail-note">Fresh confirmation is always required.</span>
                )}
              </div>
            </article>
          );
        })}
        {visible.length === 0 && (
          <section className="empty-state compact">
            <h2>No matching tools</h2>
            <p>Try a different search or risk filter.</p>
          </section>
        )}
      </div>
    </div>
  );
}
