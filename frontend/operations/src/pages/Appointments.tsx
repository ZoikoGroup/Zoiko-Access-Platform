import { useCallback, useEffect, useState } from "react";
import { cancelBooking, confirmBooking, listBookings, type Staff } from "../api";
import type { BookingRow } from "../types";
import { Card, Pill, Table, shortId, type PillKind } from "../ui";

const STATUS: Record<BookingRow["status"], { text: string; kind: PillKind }> = {
  requested: { text: "Needs confirmation", kind: "warn" },
  confirmed: { text: "Confirmed", kind: "ok" },
  cancelled: { text: "Cancelled", kind: "bad" },
};

function when(local: string, zone: string): string {
  const [d, t = ""] = local.split("T");
  return `${d} ${t.slice(0, 5)} (${zone})`;
}

export function Appointments({ staff }: { staff: Staff | null }) {
  const [rows, setRows] = useState<BookingRow[] | null>(null);
  const [filter, setFilter] = useState<"all" | BookingRow["status"]>("all");
  const [busy, setBusy] = useState("");
  const [asking, setAsking] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!staff) return;
    try { setRows(await listBookings()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load bookings"); }
  }, [staff]);

  useEffect(() => {
    setRows(null);
    void load();
    const timer = setInterval(() => void load(), 10_000);
    return () => clearInterval(timer);
  }, [load]);

  async function run(id: string, work: (id: string) => Promise<unknown>) {
    setBusy(id); setError("");
    try { await work(id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(""); setAsking(""); }
  }

  if (!staff) return <NoStaff />;
  const shown = (rows ?? []).filter((b) => filter === "all" || b.status === filter);
  const waiting = (rows ?? []).filter((b) => b.status === "requested").length;

  return (
    <>
      <div className="hero">
        <div>
          <h1>Appointments</h1>
          <p>Bookings for {staff.organizationName}. Patients request; staff confirm. {waiting > 0 && <b>{waiting} waiting for confirmation.</b>}</p>
        </div>
        <span className="spacer" />
        <label className="chip">Show
          <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} aria-label="Filter by status">
            <option value="all">all</option>
            <option value="requested">needs confirmation</option>
            <option value="confirmed">confirmed</option>
            <option value="cancelled">cancelled</option>
          </select>
        </label>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <Card title={`${shown.length} booking${shown.length === 1 ? "" : "s"}`} sub="Patients appear as ids: names are not stored yet (DEV-024).">
          {rows === null ? <div className="empty">Loading…</div> : (
            <Table headers={["When (clinic time)", "Service", "Location", "Patient", "Status", "Actions"]}
              empty="No bookings match."
              rows={shown.map((b) => [
                when(b.local_start, b.local_zone), b.service.name, b.location.name,
                <span key="p" className="mono">{shortId(b.person)}</span>,
                <Pill key="s" kind={STATUS[b.status].kind}>{STATUS[b.status].text}</Pill>,
                <span key="a">
                  {b.status === "requested" && (
                    <button className="btn" disabled={busy === b.id} onClick={() => void run(b.id, confirmBooking)}>Confirm</button>
                  )}{" "}
                  {b.status !== "cancelled" && asking !== b.id && (
                    <button className="btn ghost" onClick={() => setAsking(b.id)}>Cancel</button>
                  )}
                  {asking === b.id && (
                    <>
                      <button className="btn" disabled={busy === b.id} onClick={() => void run(b.id, cancelBooking)}>Yes, cancel</button>{" "}
                      <button className="btn ghost" onClick={() => setAsking("")}>Keep</button>
                    </>
                  )}
                </span>,
              ])} />
          )}
        </Card>
      </div>
    </>
  );
}

export function NoStaff() {
  return (
    <div className="grid">
      <Card title="Your account cannot manage this">
        <div className="empty">
          Services and appointments are managed by clinic operations staff and administrators. Your account does not
          hold that role at an organization, so there is nothing for you to manage here. Ask an administrator,
          or sign in with a clinic-operations account.
        </div>
      </Card>
    </div>
  );
}
