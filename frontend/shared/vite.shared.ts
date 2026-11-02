// What every app's vite.config.ts needs to use the code in frontend/shared.
// Shared files live outside each app, so "react" would not be found from there:
// point it at the app's own copy so there is exactly one React per app.
import path from "node:path";

export function sharedConfig(appDir: string) {
  const modules = (name: string) => path.resolve(appDir, "node_modules", name);
  return {
    alias: {
      "@shared": path.resolve(appDir, "../shared/src"),
      react: modules("react"),
      "react-dom": modules("react-dom"),
    },
    allowOutside: path.resolve(appDir, ".."),
    // No PostCSS here. An inline (even empty) config stops Vite searching parent folders, where the
    // repository root holds the other team's Tailwind postcss.config.mjs that these apps must not load.
    css: { postcss: { plugins: [] as never[] } },
  };
}
