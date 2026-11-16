import { useCallback, useEffect, useState } from "react";
import { BookingHistory } from "@shared/BookingHistory";
import { CLOSED_DAYS, claimWork, getWorkHistory, listWork, releaseWork, type Staff } from "../api";
import type { WorkItem } from "../types";
import type { PageId } from "../nav";
import { Card, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

const WHERE: Record<WorkItem["kind"], { page: PageId; text: string }> = {
  confirm_booking: { page: "appointments", text: "Open appointments" },
  decide_referral: { page: "referrals", text: "Open referrals" },
  answer_message: { page: "messages", text: "Open messages" },
  answer_data_request: { page: "rights", text: "Open data requests" },
};

/** "overdue by 3 h" / "due in 20 min": the time left against the clinic's response target. */
function dueText(item: WorkItem): string {
  const minutes = Math.round((new Date(item.due_at).getTime() - Date.now()) / 60_000);
  const span = Math.abs(minutes);
  const text = span < 60 ? `${Math.max(span, 1)} min` : span < 48 * 60 ? `${Math.round(span / 60)} h` : `${Math.round(span / 1440)} days`;
  return minutes < 0 ? `overdue by ${text}` : `due in ${text}`;
}

/** The clinic's open work: what still has to be done, who has taken it, and what is overdue. The
 *  list opens and closes by itself as bookings, referrals and messages are handled. */
export function WorkQueue({ staff, onGo }: { staff: Staff | null; onGo: (page: PageId) => void }) {
  const [filter, setFilter] = useState<"" | "mine" | "unassigned" | "closed">("");
  const [data, setData] = useState<{ me: string; items: WorkItem[] } | null>(null);
  const [busy, setBusy] = useState("");
  const [historyFor, setHistoryFor] = useState("");            // the item whose steps are shown
  const loadHistory = useCallback(() => getWorkHistory(historyFor), [historyFor]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!staff) return;
    try { setData(await listWork(filter)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the work queue"); }
  }, [staff, filter]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15_000);
    return () => clearInterval(timer);
  }, [load]);

  async function act(id: string, run: (id: string) => Promise<unknown>) {
    setBusy(id); setError("");
    try { await run(id); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work: someone else may have taken it"); await load(); }
    finally { setBusy(""); }
  }

  if (!staff) return <NoStaff />;
  const items = data?.items ?? [];
  const closedView = filter === "closed";
  const late = closedView ? 0 : items.filter((i) => i.overdue).length;
  return (
    <>
      <div className="hero">
        <div>
          <h1>Work queue</h1>
          <p>{closedView ? `Work ${staff.organizationName} has closed recently.` : `What ${staff.organizationName} still has to do. Items appear when a patient asks for something and disappear when it is handled.`} {late > 0 && <b>{late} overdue.</b>}</p>
        </div>
        <span className="spacer" />
        <label className="chip">Show
          <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} aria-label="Filter the work queue">
            <option value="">everything</option>
            <option value="mine">taken by me</option>
            <option value="unassigned">not taken yet</option>
            <option value="closed">closed in the last {CLOSED_DAYS} days</option>
          </select>
        </label>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <Card
          title={closedView ? `${items.length} item${items.length === 1 ? "" : "s"} closed in the last ${CLOSED_DAYS} days`
            : `${items.length} open item${items.length === 1 ? "" : "s"}`}
          sub={closedView ? "Newest first. Open the history to see who took it and who closed it."
            : "The one due first is at the top. Taking an item tells colleagues you are on it."}>
          {data === null ? <div className="empty">Loading…</div> : closedView ? (
            <Table headers={["Work", "Closed", "Result", "Actions"]} empty={`Nothing was closed in the last ${CLOSED_DAYS} days.`}
              rows={items.map((i) => [
                i.label,
                i.closed_at ? new Date(i.closed_at).toLocaleString() : "",
                <Pill key="r" kind={i.status === "done" ? "ok" : "info"}>{i.status === "done" ? "Done" : "No longer needed"}</Pill>,
                <span key="a">
                  <button className="btn ghost" onClick={() => setHistoryFor(historyFor === i.id ? "" : i.id)}>{historyFor === i.id ? "Hide history" : "History"}</button>
                </span>,
              ])} />
          ) : (
            <Table headers={["Work", "Due", "Taken by", "Actions"]} empty="Nothing is waiting. All clear."
              rows={items.map((i) => [
                i.label,
                i.overdue ? <Pill key="d" kind="bad">{dueText(i)}</Pill> : dueText(i),
                i.assigned_to ? (i.assigned_to === data.me ? <Pill key="m" kind="info">you</Pill> : i.assigned_to_name || "a colleague") : <Pill key="u" kind="warn">not taken</Pill>,
                <span key="a">
                  {i.assigned_to === "" && <button className="btn" disabled={busy === i.id} onClick={() => void act(i.id, claimWork)}>Take it</button>}
                  {i.assigned_to === data.me && <button className="btn ghost" disabled={busy === i.id} onClick={() => void act(i.id, releaseWork)}>Give back</button>}{" "}
                  <button className="btn ghost" onClick={() => onGo(WHERE[i.kind].page)}>{WHERE[i.kind].text}</button>{" "}
                  <button className="btn ghost" onClick={() => setHistoryFor(historyFor === i.id ? "" : i.id)}>{historyFor === i.id ? "Hide history" : "History"}</button>
                </span>,
              ])} />
          )}
        </Card>
      </div>
      {historyFor && (
        <div className="grid">
          <Card title="History of this item" sub="Who took it, gave it back or closed it, oldest first. It holds nothing about the patient."
            action={<button className="btn ghost" onClick={() => setHistoryFor("")}>Close</button>}>
            <BookingHistory key={historyFor} load={loadHistory} noun="item" />
          </Card>
        </div>
      )}
    </>
  );
}
