import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser talks only to this dev server; it forwards API calls to the backend.
// Where the backend runs: Docker publishes it on 127.0.0.1:8000.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/healthz": backend,
      "/readyz": backend,
    },
  },
});
