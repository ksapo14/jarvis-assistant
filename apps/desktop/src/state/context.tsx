import {
  disable as disableAutostart,
  enable as enableAutostart,
  isEnabled as isAutostartEnabled,
} from "@tauri-apps/plugin-autostart";
import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from "react";
import { AssistantApi } from "@/api/client";
import { EventSocket } from "@/api/websocket";
import { AutostartCoordinator } from "@/autostart";
import { mergeSettingsUpdate } from "@/settings-state";
import { SingleFlight } from "@/single-flight";
import type {
  ActivityRecord,
  AssistantSettings,
  AssistantSnapshot,
  ConfirmationRequest,
  MicrophoneDevice,
  PermissionLevel,
  ProviderStatus,
  ServerEvent,
  ToolDefinition,
} from "@/types";

const initialSnapshot: AssistantSnapshot = {
  state: "idle",
  detail: "Connecting to the local assistant…",
  liveTranscript: "",
  finalTranscript: "",
  response: "",
  recentAction: "",
  microphoneActive: false,
  wakeWordPaused: false,
  voiceMuted: false,
  connected: false,
  updatedAt: new Date().toISOString(),
};

const initialSettings: AssistantSettings = {
  launchOnStartup: false,
  minimizeToTray: true,
  playActivationSound: true,
  saveConversationHistory: true,
  developerMode: false,
  wakeWordEnabled: true,
  wakePhrase: "Hey Jarvis",
  wakeSensitivity: 0.55,
  microphoneDevice: null,
  pushToTalkShortcut: "Ctrl+Space",
  globalShortcut: "Ctrl+Shift+J",
  piperExecutablePath: "",
  piperModelPath: "",
  speechRate: 1,
  speechVolume: 0.9,
  voiceMuted: false,
  preferredApplications: {},
  toolPermissions: {},
};

interface StoreState {
  snapshot: AssistantSnapshot;
  confirmation: ConfirmationRequest | null;
  settings: AssistantSettings;
  tools: ToolDefinition[];
  history: ActivityRecord[];
  providers: ProviderStatus[];
  microphones: MicrophoneDevice[];
  controlPending: "starting" | "stopping" | "cancelling" | null;
  loading: boolean;
  uiError: string | null;
}

type StoreAction =
  | { type: "loaded"; payload: Partial<Omit<StoreState, "loading" | "uiError">> }
  | { type: "backend_event"; payload: ServerEvent }
  | { type: "connected"; payload: boolean }
  | { type: "settings"; payload: AssistantSettings }
  | { type: "tools"; payload: ToolDefinition[] }
  | { type: "history"; payload: ActivityRecord[] }
  | {
      type: "control_pending";
      payload: StoreState["controlPending"];
    }
  | { type: "error"; payload: string | null };

function reducer(state: StoreState, action: StoreAction): StoreState {
  switch (action.type) {
    case "loaded":
      return { ...state, ...action.payload, loading: false };
    case "connected":
      return {
        ...state,
        snapshot: {
          ...state.snapshot,
          connected: action.payload,
          detail: action.payload ? state.snapshot.detail : "Backend disconnected — reconnecting",
        },
      };
    case "settings":
      return { ...state, settings: action.payload };
    case "tools":
      return { ...state, tools: action.payload };
    case "history":
      return { ...state, history: action.payload };
    case "control_pending":
      return {
        ...state,
        controlPending: action.payload,
        snapshot: action.payload
          ? {
              ...state.snapshot,
              detail:
                action.payload === "starting"
                  ? "Starting the microphone…"
                  : action.payload === "stopping"
                    ? "Stopping listening…"
                    : "Cancelling the current operation…",
            }
          : state.snapshot,
      };
    case "error":
      return { ...state, uiError: action.payload };
    case "backend_event": {
      const event = action.payload;
      if (event.type === "snapshot") {
        return { ...state, snapshot: { ...event.payload, connected: true } };
      }
      if (event.type === "status_changed") {
        return {
          ...state,
          snapshot: {
            ...state.snapshot,
            state: event.payload.state,
            detail: event.payload.detail,
            microphoneActive: ["listening", "transcribing"].includes(event.payload.state),
            updatedAt: event.payload.timestamp,
          },
        };
      }
      if (event.type === "partial_transcript") {
        return {
          ...state,
          snapshot: { ...state.snapshot, liveTranscript: event.payload.text },
        };
      }
      if (event.type === "final_transcript") {
        return {
          ...state,
          snapshot: {
            ...state.snapshot,
            liveTranscript: "",
            finalTranscript: event.payload.text,
          },
        };
      }
      if (event.type === "assistant_response") {
        return {
          ...state,
          snapshot: { ...state.snapshot, response: event.payload.text },
        };
      }
      if (event.type === "tool_proposal") {
        return {
          ...state,
          snapshot: { ...state.snapshot, recentAction: `Proposed ${event.payload.name}` },
        };
      }
      if (event.type === "confirmation_request") {
        return { ...state, confirmation: event.payload };
      }
      if (event.type === "confirmation_resolved") {
        return { ...state, confirmation: null };
      }
      if (event.type === "tool_execution_result") {
        const toolValue = event.payload.tool_name ?? event.payload.name;
        const tool = typeof toolValue === "string" ? toolValue : "Desktop action";
        const success = Boolean(event.payload.success ?? true);
        return {
          ...state,
          snapshot: {
            ...state.snapshot,
            recentAction: `${tool} · ${success ? "completed" : "failed"}`,
          },
        };
      }
      if (event.type === "settings_updated") {
        return { ...state, settings: mergeSettingsUpdate(state.settings, event.payload) };
      }
      if (event.type === "cancelled") {
        return {
          ...state,
          controlPending: null,
          confirmation: null,
          snapshot: { ...state.snapshot, state: "idle", detail: event.payload.reason },
        };
      }
      if (event.type === "error") {
        return {
          ...state,
          snapshot: {
            ...state.snapshot,
            state: "error",
            detail: event.payload.message,
            error: event.payload.message,
          },
        };
      }
      return state;
    }
  }
}

interface StoreActions {
  toggleListening: () => Promise<void>;
  cancel: () => Promise<void>;
  sendCommand: (text: string) => Promise<void>;
  decideConfirmation: (approved: boolean) => Promise<void>;
  updateSettings: (patch: Partial<AssistantSettings>) => Promise<void>;
  updateTool: (
    name: string,
    patch: { enabled?: boolean; permission?: PermissionLevel },
  ) => Promise<void>;
  clearData: () => Promise<void>;
  refreshHistory: () => Promise<void>;
  dismissError: () => void;
  reportError: (message: string) => void;
}

const StoreContext = createContext<(StoreState & StoreActions) | null>(null);

const api = new AssistantApi();
const autostart = new AutostartCoordinator({
  isEnabled: isAutostartEnabled,
  enable: enableAutostart,
  disable: disableAutostart,
});
const BOOTSTRAP_RETRY_MIN_MS = 300;
const BOOTSTRAP_RETRY_MAX_MS = 5_000;

export function AssistantProvider({ children }: PropsWithChildren) {
  const controlFlight = useRef(new SingleFlight());
  const [state, dispatch] = useReducer(reducer, {
    snapshot: initialSnapshot,
    confirmation: null,
    settings: initialSettings,
    tools: [],
    history: [],
    providers: [],
    microphones: [],
    controlPending: null,
    loading: true,
    uiError: null,
  });

  const report = useCallback((error: unknown) => {
    dispatch({ type: "error", payload: error instanceof Error ? error.message : String(error) });
  }, []);

  useEffect(() => {
    let socket: EventSocket | null = null;
    let disposed = false;
    void (async () => {
      let attempt = 0;
      while (!disposed) {
        try {
          await api.initialize();
          const [snapshot, loadedSettings, tools, history, providers, microphones, confirmations] =
            await Promise.all([
              api.state(),
              api.settings(),
              api.tools(),
              api.history(),
              api.providers().catch(() => []),
              api.microphones().catch(() => []),
              api.pendingConfirmations().catch(() => []),
            ]);
          if (disposed) return;
          let settings = loadedSettings;
          let startupWarning: Error | undefined;
          if (window.__TAURI_INTERNALS__) {
            const reconciliation = await autostart.reconcile(
              settings.launchOnStartup,
              async (enabled) => {
                settings = await api.updateSettings({ launchOnStartup: enabled });
              },
            );
            startupWarning = reconciliation.warning;
            settings = { ...settings, launchOnStartup: reconciliation.enabled };
          }
          if (disposed) return;
          dispatch({
            type: "loaded",
            payload: {
              snapshot,
              settings,
              tools,
              history,
              providers,
              microphones,
              confirmation: confirmations[0] ?? null,
            },
          });
          dispatch({ type: "error", payload: startupWarning?.message ?? null });
          socket = new EventSocket({
            url: api.websocketUrl(),
            token: api.sessionToken(),
            resolveConnection: async () => {
              await api.initialize();
              return { url: api.websocketUrl(), token: api.sessionToken() };
            },
            onEvent: (event) => dispatch({ type: "backend_event", payload: event }),
            onConnectionChange: (connected) => {
              dispatch({ type: "connected", payload: connected });
              if (connected) {
                void api
                  .pendingConfirmations()
                  .catch(() => [])
                  .then((pending) => {
                    if (!disposed) {
                      dispatch({
                        type: "loaded",
                        payload: { confirmation: pending[0] ?? null },
                      });
                    }
                  });
              }
            },
          });
          socket.connect();
          return;
        } catch (error) {
          if (disposed) return;
          if (attempt === 0) report(error);
          dispatch({ type: "loaded", payload: {} });
          dispatch({ type: "connected", payload: false });
          const delay = Math.min(BOOTSTRAP_RETRY_MAX_MS, BOOTSTRAP_RETRY_MIN_MS * 2 ** attempt);
          attempt += 1;
          await new Promise((resolve) => window.setTimeout(resolve, delay));
        }
      }
    })();
    return () => {
      disposed = true;
      socket?.disconnect();
    };
  }, [report]);

  const runControlAction = useCallback(
    async (
      pending: Exclude<StoreState["controlPending"], null>,
      operation: () => Promise<void>,
    ) => {
      await controlFlight.current.run(async () => {
        dispatch({ type: "error", payload: null });
        dispatch({ type: "control_pending", payload: pending });
        try {
          await operation();
        } catch (error) {
          report(error);
        } finally {
          try {
            const snapshot = await api.state();
            dispatch({
              type: "backend_event",
              payload: { type: "snapshot", payload: snapshot },
            });
          } catch {
            // The WebSocket remains authoritative if a refresh races a backend restart.
          }
          dispatch({ type: "control_pending", payload: null });
        }
      });
    },
    [report],
  );

  const toggleListening = useCallback(async () => {
    if (["listening", "transcribing"].includes(state.snapshot.state)) {
      await runControlAction("stopping", () => api.stopListening());
    } else if (state.snapshot.state === "speaking") {
      await runControlAction("cancelling", () => api.cancel());
    } else {
      await runControlAction("starting", () => api.startListening());
    }
  }, [runControlAction, state.snapshot.state]);

  const cancel = useCallback(async () => {
    await runControlAction("cancelling", () => api.cancel());
  }, [runControlAction]);

  const sendCommand = useCallback(
    async (text: string) => {
      try {
        await api.sendCommand(text);
      } catch (error) {
        report(error);
      }
    },
    [report],
  );

  const decideConfirmation = useCallback(
    async (approved: boolean) => {
      if (!state.confirmation) return;
      try {
        await api.decideConfirmation(state.confirmation, approved);
        dispatch({
          type: "backend_event",
          payload: {
            type: "confirmation_resolved",
            payload: { id: state.confirmation.id, decision: approved ? "yes" : "no" },
          },
        });
      } catch (error) {
        report(error);
      }
    },
    [report, state.confirmation],
  );

  const updateSettings = useCallback(
    async (patch: Partial<AssistantSettings>) => {
      try {
        if (typeof patch.launchOnStartup === "boolean" && window.__TAURI_INTERNALS__) {
          const settings = await autostart.update(patch.launchOnStartup, () =>
            api.updateSettings(patch),
          );
          dispatch({ type: "settings", payload: settings });
          return;
        }
        const settings = await api.updateSettings(patch);
        dispatch({ type: "settings", payload: settings });
      } catch (error) {
        report(error);
      }
    },
    [report],
  );

  const updateTool = useCallback(
    async (name: string, patch: { enabled?: boolean; permission?: PermissionLevel }) => {
      try {
        await api.updateTool(name, patch);
        dispatch({ type: "tools", payload: await api.tools() });
      } catch (error) {
        report(error);
      }
    },
    [report],
  );

  const clearData = useCallback(async () => {
    try {
      if (window.__TAURI_INTERNALS__) await autostart.clear(() => api.clearData());
      else await api.clearData();
      dispatch({ type: "history", payload: [] });
      dispatch({ type: "settings", payload: await api.settings() });
      dispatch({ type: "tools", payload: await api.tools() });
    } catch (error) {
      report(error);
    }
  }, [report]);

  const refreshHistory = useCallback(async () => {
    try {
      dispatch({ type: "history", payload: await api.history() });
    } catch (error) {
      report(error);
    }
  }, [report]);

  const reportError = useCallback((message: string) => {
    dispatch({ type: "error", payload: message });
  }, []);

  const value = useMemo(
    () => ({
      ...state,
      toggleListening,
      cancel,
      sendCommand,
      decideConfirmation,
      updateSettings,
      updateTool,
      clearData,
      refreshHistory,
      dismissError: () => dispatch({ type: "error", payload: null }),
      reportError,
    }),
    [
      cancel,
      clearData,
      decideConfirmation,
      refreshHistory,
      reportError,
      sendCommand,
      state,
      toggleListening,
      updateSettings,
      updateTool,
    ],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useAssistant() {
  const context = useContext(StoreContext);
  if (!context) throw new Error("useAssistant must be used inside AssistantProvider");
  return context;
}
