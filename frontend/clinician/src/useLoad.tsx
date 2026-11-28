import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string;
  denied: boolean;     // the server said this role may not see this screen
  loading: boolean;
  asOf: number | null; // when the data on screen was last loaded successfully
}

/** Load data on mount and refresh it every `everyMs`. Keeps the last good data on a failed refresh. */
export function useLoad<T>(load: () => Promise<T>, everyMs = 30_000): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, error: "", denied: false, loading: true, asOf: null });
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(async () => {
    try {
      const data = await loadRef.current();
      setState({ data, error: "", denied: false, loading: false, asOf: Date.now() });
    } catch (e) {
      const denied = e instanceof ApiError && e.code === "POLICY_DENIED";
      setState((cur) => ({ data: denied ? null : cur.data, denied, asOf: denied ? null : cur.asOf,
        error: e instanceof Error ? e.message : "Could not load", loading: false }));
    }
  }, []);

  useEffect(() => {
    void run();
    const timer = setInterval(() => void run(), everyMs);
    return () => clearInterval(timer);
  }, [run, everyMs]);

  return state;
}

/** Shows loading, "your role cannot see this", or an error, and otherwise renders the data. */
export function Gate<T>({ result, children }: { result: Loaded<T>; children: (data: T) => ReactNode }) {
  if (result.denied) {
    return <div className="notice" role="status">Your role does not include this screen.</div>;
  }
  if (result.data === null) {
    return result.loading ? <div className="notice">Loading…</div> : <div className="error">{result.error}</div>;
  }
  return (
    <>
      {result.error && (
        <div className="error" role="alert">
          {result.error}
          {result.asOf && ` The information below is old: it was last updated at ${new Date(result.asOf).toLocaleTimeString()}.`}
        </div>
      )}
      {children(result.data)}
    </>
  );
}
