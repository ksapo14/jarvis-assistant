/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ASSISTANT_URL?: string;
  readonly VITE_ASSISTANT_SESSION_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  __TAURI_INTERNALS__?: unknown;
}
