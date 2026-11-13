import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CrashGuard, installFatalNotice } from "@shared/CrashGuard";
import App from "./App";
import "./styles.css";
import "@shared/auth.css";
import "./partners.css";

installFatalNotice();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <CrashGuard>
      <App />
    </CrashGuard>
  </StrictMode>,
);
