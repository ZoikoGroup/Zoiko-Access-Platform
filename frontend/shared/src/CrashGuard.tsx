import { Component, type ReactNode } from "react";

/** Without this, any crash while drawing a screen leaves a blank white page and no message.
 *  It shows what went wrong, and a way out that clears the saved sign-in. */
export class CrashGuard extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error("A screen crashed:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const where = (error.stack ?? "").split("\n").slice(0, 6).join("\n");
    return (
      <div role="alert" style={{ maxWidth: 760, margin: "48px auto", padding: "0 16px", font: "15px system-ui, sans-serif" }}>
        <h1 style={{ fontSize: 22 }}>Something went wrong on this screen</h1>
        <p>The page could not be drawn. Nothing was lost on the server. Reloading usually fixes it; if it
          does not, signing out and back in does. If it keeps happening, send this text to support:</p>
        <pre style={{ whiteSpace: "pre-wrap", background: "#f6f1ef", padding: 12, borderRadius: 8, overflow: "auto" }}>
          {error.message}
          {where ? `\n\n${where}` : ""}
        </pre>
        <p>
          <button type="button" onClick={() => window.location.reload()}>Reload the page</button>{" "}
          <button type="button" onClick={() => {
            try { sessionStorage.clear(); } catch { /* storage may be blocked */ }
            window.location.reload();
          }}>Sign out and reload</button>
        </p>
      </div>
    );
  }
}

/** Errors outside the screens (a failed start-up, a stray rejection) never reach CrashGuard, so
 *  they get a banner instead of a silent blank page. */
export function installFatalNotice(): void {
  const show = (text: string) => {
    if (document.getElementById("fatal-notice")) return;
    const bar = document.createElement("div");
    bar.id = "fatal-notice";
    bar.setAttribute("role", "alert");
    bar.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99999;background:#fff3f0;color:#7a1d12;"
      + "padding:10px 16px;font:14px system-ui,sans-serif;border-bottom:2px solid #b3261e;white-space:pre-wrap";
    bar.textContent = `The page hit an error and may look blank or stuck. Details: ${text}`;
    document.body.prepend(bar);
  };
  window.addEventListener("error", (e) => show(e.message || String(e.error)));
  window.addEventListener("unhandledrejection", (e) => {
    const reason = e.reason as { message?: string } | undefined;
    show(`a request or task failed: ${reason?.message ?? String(e.reason)}`);
  });
}
