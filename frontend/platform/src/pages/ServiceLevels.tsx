import { useCallback, useEffect, useState } from "react";
import { getSlo, type SloReport, type Indicator } from "../api";
import { Card, Pill } from "../ui";

function pct(v: number | null): string {
  return v === null ? "no requests yet" : `${(v * 100).toFixed(2)}%`;
}

function Verdict({ i }: { i: Indicator }) {
  if (i.meeting === null) return <Pill kind="info">No verdict</Pill>;
  return i.meeting ? <Pill kind="ok">Meeting the target</Pill> : <Pill kind="bad">Missing the target</Pill>;
}

/** How the service is doing against its two proposed targets, as seen by one web process. */
export function ServiceLevels() {
  const [data, setData] = useState<SloReport | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await getSlo()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);
  return (
    <>
      <div className="hero"><div>
        <h1>Service levels</h1>
        <p>Two measures of whether the service is working for people: requests that did not fail with a server error, and requests answered within {data ? data.targets.latency_seconds * 1000 : 750} ms. The targets are proposals (99.9% and 95%), not commitments. A green number here does not prove a booking or handoff was correct.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {data && (
        <div className="grid">
          {data.windows.map((w) => (
            <Card key={w.window_minutes} title={`Last ${w.window_minutes} minutes`} sub={`${w.requests} request${w.requests === 1 ? "" : "s"}`}>
              <div className="appt"><div className="info">
                <b>Availability <Verdict i={w.availability} /></b>
                <small>{pct(w.availability.value)} of requests succeeded (target {pct(w.availability.target)}). Error budget burning at {w.availability.burn_rate ?? "-"}x the allowed rate.</small>
              </div></div>
              <div className="appt"><div className="info">
                <b>Speed <Verdict i={w.latency} /></b>
                <small>{pct(w.latency.value)} answered within {data.targets.latency_seconds * 1000} ms (target {pct(w.latency.target)}). Burn rate {w.latency.burn_rate ?? "-"}x.</small>
              </div></div>
            </Card>
          ))}
          <Card title="Busiest routes" sub="URL patterns only. No customer, person or address appears here.">
            {data.routes.length === 0 ? <div className="empty">No requests yet.</div> : data.routes.map((r) => (
              <div key={r.route} className="appt"><div className="info">
                <b>{r.route}</b>
                <small>{r.requests} request{r.requests === 1 ? "" : "s"}, {r.server_errors} server error{r.server_errors === 1 ? "" : "s"}, mean {(r.mean_seconds * 1000).toFixed(0)} ms</small>
              </div></div>
            ))}
          </Card>
          <Card title="What these numbers do not tell you">
            {data.limits.map((l) => <div key={l} className="appt"><div className="info"><small>{l}</small></div></div>)}
          </Card>
        </div>
      )}
    </>
  );
}
