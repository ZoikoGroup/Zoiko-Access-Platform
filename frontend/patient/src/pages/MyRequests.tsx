import { useEffect, useState } from "react";
import { answerMyRequest, disputeMyResult, getMyRequestsPage, getMyTrend, getMyValues, type DisputeReason, type MyRequest, type MyValue, type TrendPoint } from "../api";
import { Card, Pill, type PillKind } from "../ui";

/** A small line of the numeric points, with the normal range shaded when the lab gave one. */
function Sparkline({ points }: { points: TrendPoint[] }) {
  const nums = points.filter((p) => typeof p.value === "number") as (TrendPoint & { value: number })[];
  if (nums.length < 2) return null;
  const w = 240, h = 60, pad = 4;
  const low = nums[nums.length - 1].low, high = nums[nums.length - 1].high;
  const all = [...nums.map((p) => p.value), ...(low != null ? [low] : []), ...(high != null ? [high] : [])];
  const min = Math.min(...all), max = Math.max(...all), span = max - min || 1;
  const x = (i: number) => pad + (i * (w - 2 * pad)) / (nums.length - 1);
  const y = (v: number) => h - pad - ((v - min) * (h - 2 * pad)) / span;
  return (
    <svg width={w} height={h} role="img" aria-label="Trend line" style={{ display: "block", margin: "4px 0" }}>
      {low != null && high != null && <rect x={pad} width={w - 2 * pad} y={y(high)} height={Math.max(1, y(low) - y(high))} fill="currentColor" opacity={0.08} />}
      <polyline fill="none" stroke="currentColor" strokeWidth={2} points={nums.map((p, i) => `${x(i)},${y(p.value)}`).join(" ")} />
      {nums.map((p, i) => <circle key={p.request} cx={x(i)} cy={y(p.value)} r={3} fill="currentColor" />)}
    </svg>
  );
}

const KIND: Record<MyRequest["kind"], string> = {
  lab_order: "Test or scan", pharmacy_request: "Pharmacy", homecare_referral: "Home care",
};

// Plain words for where each request stands. A ready result is never described as read by a doctor.
function words(r: MyRequest): { text: string; kind: PillKind; hint: string } {
  switch (r.state) {
    case "requested": return { text: "Sent", kind: "info", hint: `Sent to ${r.partner}. Waiting for them to accept it.` };
    case "accepted": return { text: "Accepted", kind: "info", hint: `${r.partner} has accepted it.` };
    case "in_progress": return { text: "In progress", kind: "info", hint: `${r.partner} is working on it.` };
    case "declined": return { text: "Not possible", kind: "bad", hint: `${r.partner} could not do this. Your clinic will get in touch with you.` };
    case "result_available": return { text: "Result ready", kind: "warn", hint: "The result is ready. Your doctor has not reviewed it yet, so please do not take it as medical advice. Your clinic will tell you when they have." };
    case "reviewed": return { text: "Reviewed by your doctor", kind: "ok", hint: "Your doctor has reviewed the result." };
    case "cancelled": return { text: "Cancelled", kind: "info", hint: "Your clinic cancelled this request." };
  }
}

const OPEN = ["requested", "accepted", "in_progress"];

// Why a result is held from you (DEV-385), in plain words.
const HELD: Record<string, string> = {
  identity_check: "This result is on hold while your clinic checks that your records are not mixed up with someone else's. You will see it once that is done.",
  dispute: "You told us something is wrong with this result. It is on hold until your doctor has looked into it.",
  not_theirs: "Your clinic agreed this result is not yours. It has been removed from your record and they are having it corrected.",
};
const REASONS: { code: DisputeReason; text: string }[] = [
  { code: "not_me", text: "This result is not mine" },
  { code: "wrong_test", text: "I did not have this test" },
  { code: "wrong_values", text: "The values look wrong" },
];

/** "Something wrong with this result?": a reason from a list (no free text), sent to the clinic. */
function Dispute({ id, onDone }: { id: string; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<DisputeReason>("not_me");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!open) return <span><button className="btn ghost" onClick={() => setOpen(true)}>Something wrong with this result?</button></span>;
  return (
    <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      <select aria-label="What is wrong" value={reason} onChange={(e) => setReason(e.target.value as DisputeReason)}>
        {REASONS.map((x) => <option key={x.code} value={x.code}>{x.text}</option>)}
      </select>
      <button className="btn" disabled={busy} onClick={() => {
        setBusy(true); setError("");
        disputeMyResult(id, reason).then(() => { setOpen(false); onDone(); })
          .catch((e: Error) => setError(e.message)).finally(() => setBusy(false));
      }}>Tell my clinic</button>
      <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
      {error && <small role="alert" style={{ color: "var(--bad)" }}>{error}</small>}
    </span>
  );
}

export function MyRequests() {
  const [rows, setRows] = useState<MyRequest[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [values, setValues] = useState<Record<string, MyValue[]>>({});
  const [trend, setTrend] = useState<{ requestId: string; name: string; points: TrendPoint[] } | null>(null);
  const [trendBusy, setTrendBusy] = useState(false);
  async function showValues(id: string) {
    setBusy(id); setError("");
    try { const got = await getMyValues(id); setValues({ ...values, [id]: got.values }); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the values"); }
    finally { setBusy(""); }
  }
  async function openTrend(requestId: string, v: MyValue) {
    const key = v.code || v.name;
    setTrendBusy(true); setError("");
    try {
      const points = await getMyTrend(requestId, key);
      setTrend({ requestId, name: v.name, points });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the trend");
    } finally {
      setTrendBusy(false);
    }
  }
  const [more, setMore] = useState<string | null>(null);
  const load = () => getMyRequestsPage().then((p) => { setRows(p.requests); setMore(p.next_cursor ?? null); })
    .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load"));
  async function older() {
    if (!more) return;
    try { const p = await getMyRequestsPage(more); setRows([...(rows ?? []), ...p.requests]); setMore(p.next_cursor ?? null); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load older requests"); }
  }
  useEffect(() => { void load(); }, []);
  async function answer(id: string, step: "agree" | "decline" | "withdraw") {
    setBusy(id); setError("");
    try { await answerMyRequest(id, step); await load(); }
    catch (e) {
      const code = (e as { code?: string }).code;
      setError(code === "VALIDATION_FAILED" ? "Please add your name to your details first, then try again."
        : e instanceof Error ? e.message : "That did not work");
    } finally { setBusy(""); }
  }
  return (
    <>
      <div className="hero"><div>
        <h1>My Tests &amp; Requests</h1>
        <p>When your clinic asks a laboratory, pharmacy or home-care service to do something for you, it appears here with how far it has got. Your clinic goes through results with you; a day after your doctor has reviewed a result, you can also see its measured values here.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Requests (${rows?.length ?? 0})`}>
          {rows === null ? <div className="empty">Loading…</div> : rows.length === 0
            ? <div className="empty">Nothing has been requested for you.</div>
            : rows.map((r) => {
              const w = words(r);
              return (
                <div className="appt" key={r.id}>
                  <div className="info">
                    <b>{r.label}</b>
                    <small>{KIND[r.kind]} · {r.partner} · asked on {r.created_at.slice(0, 10)}</small>
                    <small>{w.hint}</small>
                    {r.held && <small role="status"><Pill kind={r.held === "not_theirs" ? "info" : "warn"}>On hold</Pill> {HELD[r.held]}</small>}
                    {r.dispute === "confirmed" && !r.held && <small>Your clinic looked into what you reported and confirmed this result is yours.</small>}
                    {r.consent === "pending" && OPEN.includes(r.state) && (
                      <small>
                        <b>Your agreement is needed.</b> {r.partner} can only do this if you agree to tell them your name and date of birth for this one request. They are told nothing else, and you can take it back at any time. If you say no, the request is cancelled.
                      </small>
                    )}
                    {r.consent === "granted" && r.consent_expires_at && (
                      <small>You agreed to share your name and date of birth for this request until {r.consent_expires_at.slice(0, 10)}.</small>
                    )}
                    {r.consent === "expired" && OPEN.includes(r.state) && <small>Your agreement has ended. Agree again if you still want this done.</small>}
                    {r.consent === "declined" && <small>You said no, so nothing was shared.</small>}
                    {r.consent === "withdrawn" && <small>You took your agreement back and what was shared has been removed.</small>}
                    {(r.consent === "pending" || r.consent === "expired") && OPEN.includes(r.state) && (
                      <span style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <button className="btn" disabled={busy === r.id} onClick={() => void answer(r.id, "agree")}>I agree to share my name and date of birth</button>
                        {r.consent === "pending" && <button className="btn ghost" disabled={busy === r.id} onClick={() => void answer(r.id, "decline")}>No, cancel this request</button>}
                      </span>
                    )}
                    {r.consent === "granted" && (
                      <span><button className="btn ghost" disabled={busy === r.id} onClick={() => void answer(r.id, "withdraw")}>Take my agreement back</button></span>
                    )}
                    {r.result_at && !r.held && r.dispute !== "not_theirs" && (
                      <Dispute id={r.id} onDone={() => void load()} />
                    )}
                    {r.values_ready && !values[r.id] && (
                      <span><button className="btn ghost" disabled={busy === r.id} onClick={() => void showValues(r.id)}>See the measured values</button></span>
                    )}
                    {values[r.id] && (
                      <div role="region" aria-label={`Measured values for ${r.label}`}>
                        <table style={{ width: "100%", fontSize: 13 }}>
                          <thead><tr><th align="left">Test</th><th align="right">Value</th><th align="left">Unit</th><th align="left">Usual range</th><th align="right">Action</th></tr></thead>
                          <tbody>
                            {values[r.id].map((v, i) => (
                              <tr key={i}>
                                <td>{v.name}</td>
                                <td align="right"><b>{String(v.value)}</b>{v.flag && v.flag !== "normal" ? ` (${v.flag})` : ""}</td>
                                <td>{v.unit ?? ""}</td>
                                <td>{v.low != null || v.high != null ? `${v.low ?? ""} to ${v.high ?? ""}` : ""}</td>
                                <td align="right">
                                  {typeof v.value === "number" && (
                                    <button
                                      className="btn ghost"
                                      style={{ padding: "0 6px", fontSize: 12 }}
                                      title="See how this value changed over time"
                                      disabled={trendBusy}
                                      onClick={() => void openTrend(r.id, v)}
                                    >
                                      Trend
                                    </button>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {trend && trend.requestId === r.id && (
                          <div role="region" aria-label={`${trend.name} over time`} style={{ marginTop: 10, padding: 10, border: "1px solid var(--line)", borderRadius: 8 }}>
                            <b>{trend.name} over time ({trend.points.length} result{trend.points.length === 1 ? "" : "s"})</b>
                            <Sparkline points={trend.points} />
                            <table style={{ width: "100%", fontSize: 12, marginTop: 4 }}>
                              <tbody>
                                {trend.points.map((p) => (
                                  <tr key={p.request}>
                                    <td>{p.at?.slice(0, 10)}</td>
                                    <td align="right"><b>{String(p.value)}</b> {p.unit}</td>
                                    <td><small>{p.label}</small></td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                            <div style={{ marginTop: 6 }}>
                              <button className="btn ghost" onClick={() => setTrend(null)}>Hide trend</button>
                            </div>
                          </div>
                        )}
                        <small style={{ display: "block", marginTop: 6 }}>Your doctor has reviewed these. If something worries you, contact your clinic. Opening them is recorded in your privacy log.</small>
                      </div>
                    )}
                  </div>
                  {r.urgency === "urgent" && <Pill kind="warn">Urgent</Pill>}
                  <Pill kind={w.kind}>{w.text}</Pill>
                </div>
              );
            })}
          {more && <div style={{ marginTop: 8 }}><button className="btn ghost" onClick={() => void older()}>Show older requests</button></div>}
        </Card>
      </div>
    </>
  );
}
