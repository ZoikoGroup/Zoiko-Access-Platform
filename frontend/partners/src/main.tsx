import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CrashGuard, installFatalNotice } from "@shared/CrashGuard";
import App from "./App";
import "./styles.css";
import "@shared/auth.css";
import "./partners.css";
import { startLanguage } from "@shared/i18n";

installFatalNotice();

startLanguage();              // DEV-402: English for now; the framework is ready

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <CrashGuard>
      <App />
    </CrashGuard>
  </StrictMode>,
);
