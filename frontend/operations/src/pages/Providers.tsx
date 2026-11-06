import { useState } from "react";
import { getProviders, verifyProviderName, withdrawProviderCheck, type Staff } from "../api";
import type { ProviderRow } from "../types";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

const next = (local: string | null, zone: string | null) =>
  local && zone ? `${local.replace("T", " ").slice(0, 16)} (${zone})` : "none booked";

export function Providers({ staff }: { staff: Staff | null }) {
  const [version, setVersion] = useState(0);
  const result = useLoad(getProviders, 30_000, version);
  const [open, setOpen] = useState("");                  // the doctor whose check form is open
  const [mode, setMode] = useState<"verify" | "withdraw">("verify");
  const [basis, setBasis] = useState("licence_register");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  if (!staff) return <NoStaff />;

  async function run(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setNote("");
    try { await work(); setNote(success); setOpen(""); setText(""); setVersion((v) => v + 1); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  function mark(p: ProviderRow) {
    const c = p.name_check;
    if (c.state === "verified") return <Pill kind="ok">Verified</Pill>;
    if (c.state === "name_changed") return <Pill kind="warn">Name changed: check again</Pill>;
    if (c.state === "withdrawn") return <Pill kind="bad">Check withdrawn</Pill>;
    return <Pill kind="info">Not checked</Pill>;
  }

  return (
    <Gate result={result}>
      {(d) => (
        <>
          <div className="hero"><div>
            <h1>Providers</h1>
            <p>The doctors of {staff.organizationName} and how busy they are in the next {d.days} days. A doctor chooses their own name.
              {d.can_manage ? " As an administrator you can record that you checked a name (for example on the licence register); patients then see a Verified mark. If the doctor changes the name, the mark goes until it is checked again." : " An administrator can record that a name was checked."}
              {" "}Qualifications and privileges are not built yet.</p>
          </div></div>
          {error && <div className="error" role="alert">{error}</div>}
          {note && <div className="success" role="status">{note}</div>}
          <div className="grid">
            <Card title={`${d.providers.length} doctor${d.providers.length === 1 ? "" : "s"}`}>
              <Table headers={["Doctor", "Name check", "Confirmed appointments", "Free times", "Next appointment"]}
                empty="No doctors have been added to this clinic yet. Add one on the Team page."
                rows={d.providers.map((p) => [
                  p.name || p.id,
                  <span key="c">{mark(p)}
                    {p.name_check.state === "verified" && <small style={{ display: "block" }}>{p.name_check.basis_text} on {p.name_check.checked_on}{p.name_check.note ? `: ${p.name_check.note}` : ""}</small>}
                    {p.name_check.state === "withdrawn" && p.name_check.reason && <small style={{ display: "block" }}>{p.name_check.reason}</small>}
                    {d.can_manage && open !== p.id && (
                      <span style={{ display: "block" }}>
                        {p.name_check.state !== "verified" && (
                          <button className="btn ghost" onClick={() => { setOpen(p.id); setMode("verify"); setError(""); setNote(""); }}>Record a check</button>
                        )}
                        {p.name_check.state === "verified" && (
                          <button className="btn ghost" onClick={() => { setOpen(p.id); setMode("withdraw"); setError(""); setNote(""); }}>Withdraw the check</button>
                        )}
                      </span>
                    )}
                    {d.can_manage && open === p.id && mode === "verify" && (
                      <span className="form-row" style={{ display: "block" }}>
                        <select aria-label={`What you checked for ${p.name || p.id}`} value={basis} onChange={(e) => setBasis(e.target.value)}>
                          {Object.entries(d.bases).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                        </select>{" "}
                        <input aria-label={`Note for ${p.name || p.id}`} placeholder="Note, e.g. register number (no patient details)" maxLength={200}
                          value={text} onChange={(e) => setText(e.target.value)} />{" "}
                        <button className="btn" disabled={busy} onClick={() => void run(() => verifyProviderName(p.id, basis, text), "Check recorded.")}>Save check</button>{" "}
                        <button className="btn ghost" onClick={() => setOpen("")}>Cancel</button>
                      </span>
                    )}
                    {d.can_manage && open === p.id && mode === "withdraw" && (
                      <span className="form-row" style={{ display: "block" }}>
                        <input aria-label={`Reason to withdraw the check for ${p.name || p.id}`} placeholder="Reason (required)" maxLength={200}
                          value={text} onChange={(e) => setText(e.target.value)} />{" "}
                        <button className="btn" disabled={busy || text.trim().length < 3} onClick={() => void run(() => withdrawProviderCheck(p.id, text), "Check withdrawn.")}>Withdraw</button>{" "}
                        <button className="btn ghost" onClick={() => setOpen("")}>Cancel</button>
                      </span>
                    )}
                  </span>,
                  p.upcoming,
                  p.open_times === 0 ? <Pill key="f" kind="warn">none free</Pill> : p.open_times,
                  next(p.next_local, p.next_zone),
                ])} />
            </Card>
          </div>
        </>
      )}
    </Gate>
  );
}
