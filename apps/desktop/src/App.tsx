import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { register, unregister } from "@tauri-apps/plugin-global-shortcut";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useEffect, useRef, useState } from "react";
import { Sidebar, type PageId } from "@/components/Sidebar";
import { ActivityPage } from "@/pages/ActivityPage";
import { AssistantPage } from "@/pages/AssistantPage";
import { GeneralPage } from "@/pages/GeneralPage";
import { ProvidersPage } from "@/pages/ProvidersPage";
import { ToolsPage } from "@/pages/ToolsPage";
import { VoicePage } from "@/pages/VoicePage";
import {
  normalizeShortcut,
  ShortcutRegistrationCoordinator,
  shortcutValidationError,
} from "@/shortcuts";
import { useAssistant } from "@/state/context";

const pages: Record<PageId, React.ReactNode> = {
  assistant: <AssistantPage />,
  history: <ActivityPage />,
  tools: <ToolsPage />,
  voice: <VoicePage />,
  providers: <ProvidersPage />,
  general: <GeneralPage />,
};

const shortcutRegistrations = new ShortcutRegistrationCoordinator(register, unregister);
const defaultShortcutSettings = {
  globalShortcut: "Ctrl+Shift+J",
  pushToTalkShortcut: "Ctrl+Space",
} as const;

export default function App() {
  const {
    snapshot,
    settings,
    uiError,
    dismissError,
    reportError,
    toggleListening,
    updateSettings,
  } = useAssistant();
  const [page, setPage] = useState<PageId>("assistant");
  const toggleListeningRef = useRef(toggleListening);
  const lastRegisteredShortcuts = useRef<{
    globalShortcut: string;
    pushToTalkShortcut: string;
  }>({ ...defaultShortcutSettings });

  useEffect(() => {
    toggleListeningRef.current = toggleListening;
  }, [toggleListening]);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      if (matchesShortcut(event, settings.pushToTalkShortcut)) {
        event.preventDefault();
        setPage("assistant");
        void toggleListening();
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [settings.pushToTalkShortcut, toggleListening]);

  useEffect(() => {
    if (!window.__TAURI_INTERNALS__) return;
    const currentWindow = getCurrentWindow();
    const shortcuts = Array.from(
      new Set([settings.globalShortcut, settings.pushToTalkShortcut].filter(Boolean)),
    );
    const invalidShortcut = shortcuts.find((shortcut) => shortcutValidationError(shortcut));
    if (invalidShortcut) {
      reportError(`Could not register invalid shortcut: ${invalidShortcut}`);
      return;
    }
    return shortcutRegistrations.replace(
      shortcuts,
      (event) => {
        if (event.state !== "Pressed") return;
        setPage("assistant");
        void currentWindow.show().then(() => currentWindow.setFocus());
        if (sameShortcut(event.shortcut, settings.pushToTalkShortcut)) {
          void toggleListeningRef.current();
        }
      },
      (error) => {
        reportError(`Could not register the configured shortcut: ${String(error)}`);
        const previous = lastRegisteredShortcuts.current;
        if (
          previous.globalShortcut !== settings.globalShortcut ||
          previous.pushToTalkShortcut !== settings.pushToTalkShortcut
        ) {
          void updateSettings(previous);
        }
      },
      () => {
        lastRegisteredShortcuts.current = {
          globalShortcut: settings.globalShortcut,
          pushToTalkShortcut: settings.pushToTalkShortcut,
        };
      },
    );
  }, [reportError, settings.globalShortcut, settings.pushToTalkShortcut, updateSettings]);

  useEffect(() => {
    if (!window.__TAURI_INTERNALS__) return;
    void invoke("set_close_behavior", {
      minimizeToTray: settings.minimizeToTray,
    }).catch((error: unknown) => {
      reportError(`Could not update the window close behavior: ${String(error)}`);
    });
  }, [reportError, settings.minimizeToTray]);

  useEffect(() => {
    if (!window.__TAURI_INTERNALS__) return;
    let unlisten: UnlistenFn | undefined;
    void listen<string>("navigate", (event) => {
      const target = event.payload as PageId;
      if (target in pages) setPage(target);
    }).then((dispose) => {
      unlisten = dispose;
    });
    return () => unlisten?.();
  }, []);

  return (
    <div className="app-shell">
      <Sidebar page={page} onNavigate={setPage} connected={snapshot.connected} />
      <main className="content" id="main-content">
        {pages[page]}
      </main>
      {uiError && (
        <div className="toast" role="alert">
          <div>
            <strong>JARVIS couldn’t complete that request</strong>
            <span>{uiError}</span>
          </div>
          <button onClick={dismissError} aria-label="Dismiss error">
            ×
          </button>
        </div>
      )}
    </div>
  );
}

function sameShortcut(left: string, right: string): boolean {
  return normalizeShortcut(left) === normalizeShortcut(right);
}

function matchesShortcut(event: KeyboardEvent, configured: string): boolean {
  const parts = normalizeShortcut(configured).split("+");
  const key = parts.at(-1) ?? "";
  const eventKey = event.code
    .toLowerCase()
    .replace(/^key/, "")
    .replace(/^digit/, "");
  const expectedKey = key === "space" ? "space" : key;
  return (
    parts.includes("ctrl") === event.ctrlKey &&
    parts.includes("shift") === event.shiftKey &&
    parts.includes("alt") === event.altKey &&
    eventKey === expectedKey
  );
}
