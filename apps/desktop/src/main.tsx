import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AssistantProvider } from "./state/context";
import "./styles/global.css";

const root = document.getElementById("root");
if (!root) throw new Error("Application root element was not found");

createRoot(root).render(
  <StrictMode>
    <AssistantProvider>
      <App />
    </AssistantProvider>
  </StrictMode>,
);
