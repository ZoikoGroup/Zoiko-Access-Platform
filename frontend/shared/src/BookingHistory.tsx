import { useEffect, useState } from "react";
import { reasonText } from "./cancelReasons";
import { Pill } from "./ui";

/** One step of an appointment, as the server words it for this reader (no patient name, and a
 *  patient never sees which staff member acted). */
export interface BookingStep {
  seq: number;
  event: string;
  text: string;
  from?: string;
  to?: string;
  reason?: string;          // appointments only
  who: string;
  at: string;
}

export interface BookingHistoryData {
  chain_ok: boolean;
  events: BookingStep[];
}

const KIND: Record<string, "ok" | "bad" | "warn" | "info"> = {
  requested: "info", confirmed: "ok", cancelled: "bad", moved_away: "warn", moved_here: "info",
  attended: "ok", no_show: "bad", reminder_sent: "info",
  opened: "info", claimed: "info", released: "warn", done: "ok",   // work items
};

const when = (iso: string) => new Date(iso).toLocaleString();

/** Every step of one appointment, oldest first. Bookings made before the history was kept show none. */
export function BookingHistory({ load, noun = "appointment" }: {
  load: () => Promise<BookingHistoryData>; noun?: string;
}) {
  const [data, setData] = useState<BookingHistoryData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    load().then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : "Could not load the history"); });
    return () => { live = false; };
  }, [load]);

  if (error) return <div className="error" role="alert">{error}</div>;
  if (!data) return <div className="empty">Loading…</div>;
  return (
    <div>
      {!data.chain_ok && (
        <div className="error" role="alert">
          This record failed its integrity check, so it may have been changed. Tell Zoiko support.
        </div>
      )}
      {data.events.length === 0 ? (
        <div className="empty">
          No history was kept for this {noun}: it was made before steps were recorded.
        </div>
      ) : (
        <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {data.events.map((e) => (
            <li key={e.seq} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
              <Pill kind={KIND[e.event] ?? "info"}>{e.text}</Pill>{" "}
              <span>{e.who}</span>
              {e.event === "cancelled" && e.reason !== undefined && <span> - reason: {reasonText(e.reason)}</span>}
              <small style={{ display: "block", color: "var(--muted)" }}>{when(e.at)}</small>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
