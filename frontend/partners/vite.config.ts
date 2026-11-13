import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { sharedConfig } from "../shared/vite.shared";

// The browser talks only to this dev server; it forwards API calls to the backend.
// Where the backend runs: Docker publishes it on 127.0.0.1:8000.
const shared = sharedConfig(__dirname);
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: shared.alias },
  css: shared.css,
  server: {
    fs: { allow: [shared.allowOutside] },
    port: 5177,
    proxy: {
      "/api": backend,
      "/healthz": backend,
      "/readyz": backend,
    },
  },
});
