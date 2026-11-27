import { useCallback, useEffect, useState } from "react";
import { getErrorBudget, getSlo, type Budget, type ErrorBudget, type SloReport, type Indicator } from "../api";
import { Card, Pill } from "../ui";

function pct(v: number | null): string {
  return v === null ? "no requests yet" : `${(v * 100).toFixed(2)}%`;
}

function Verdict({ i }: { i: Indicator }) {
  if (i.meeting === null) return <Pill kind="info">No verdict</Pill>;
  return i.meeting ? <Pill kind="ok">Meeting the target</Pill> : <Pill kind="bad">Missing the target</Pill>;
}

const BUDGET_STATE: Record<Budget["state"], { text: string; kind: "ok" | "warn" | "bad" | "info" }> = {
  healthy: { text: "Healthy", kind: "ok" },
  warning: { text: "Three quarters used", kind: "warn" },
  exhausted: { text: "Used up: releases held", kind: "bad" },
  not_enough_traffic: { text: "Not enough traffic to judge", kind: "info" },
};

/** The 30-day error budget, burn rates, and each alert with its runbook (DEV-388). */
function BudgetCard() {
  const [b, setB] = useState<ErrorBudget | null>(null);
  useEffect(() => { void getErrorBudget().then(setB).catch(() => setB(null)); }, []);
  if (!b) return null;
  const row = (name: string, x: Budget) => (
    <div className="appt" key={name}><div className="info">
      <b>{name} <Pill kind={BUDGET_STATE[x.state].kind}>{BUDGET_STATE[x.state].text}</Pill></b>
      <small>{x.failures} of {x.allowed_failures} allowed failures in {x.requests} requests ({Math.round(x.used * 100)}% used, target {(x.target * 100).toFixed(1)}%).</small>
    </div></div>
  );
  return (
    <Card title={`Error budget: last ${b.window_days} days`} sub="All web processes, kept in the database. When a budget is used up, every release waits unless a safety officer overrides it for that release.">
      {row("Availability", b.budgets.availability)}
      {row("Speed", b.budgets.latency)}
      <p><small>Burn rate, last hour: {b.burn.last_hour.availability}x (errors), {b.burn.last_hour.latency}x (slow). Last 6 hours: {b.burn.last_6_hours.availability}x, {b.burn.last_6_hours.latency}x.</small></p>
      {b.alerts.length === 0 ? <p><Pill kind="ok">No alerts</Pill></p> : b.alerts.map((a) => (
        <div key={a.alert} className="error" role="alert"><b>{a.alert}</b>: follow <code>{a.runbook}</code></div>
      ))}
      {!b.recording && <div className="notice">Request counting is off on this server: there is no budget.</div>}
      <p><small>Runbooks: {b.runbooks.map((r) => <code key={r} style={{ marginRight: 8 }}>{r.replace("docs/runbooks/", "")}</code>)}</small></p>
    </Card>
  );
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
      <BudgetCard />
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
          <Card title="Text messages" sub="Counts only: no number or text appears here.">
            <div className="appt"><div className="info">
              <b>Provider: {data.texts.provider} <Pill kind={data.texts.breaker === "open" ? "bad" : "ok"}>{data.texts.breaker === "open" ? "paused after repeated failures" : "sending normally"}</Pill></b>
              <small>{data.texts.breaker === "open" && data.texts.open_until ? `Paused until ${data.texts.open_until.slice(11, 16)} UTC; texts wait and keep their tries. ` : ""}Failures in a row: {data.texts.failures_in_a_row}. Paused {data.texts.times_opened} time{data.texts.times_opened === 1 ? "" : "s"} so far.</small>
              <small>Waiting {data.texts.queue.queued ?? 0}, sent {data.texts.queue.sent ?? 0}, given up {data.texts.queue.failed ?? 0}. Receipts: delivered {data.texts.receipts.delivered ?? 0}, undelivered {data.texts.receipts.undelivered ?? 0}, failed {data.texts.receipts.failed ?? 0}.</small>
            </div></div>
          </Card>
          <Card title="What these numbers do not tell you">
            {data.limits.map((l) => <div key={l} className="appt"><div className="info"><small>{l}</small></div></div>)}
          </Card>
        </div>
      )}
    </>
  );
}
