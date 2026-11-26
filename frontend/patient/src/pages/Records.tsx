import { useEffect, useState } from "react";
import { ApiError, getMyReferences, type RecordReference } from "../api";
import { Card, Pill } from "../ui";

const KIND: Record<string, string> = {
  consultation_note: "Consultation note", result: "Result", letter: "Letter", imaging: "Imaging", other: "Other record",
};

/** Where the patient's records are held. Zoiko Access keeps only the pointer, never the record. */
export function Records() {
  const [rows, setRows] = useState<RecordReference[] | null>(null);
  const [off, setOff] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    getMyReferences().then(setRows).catch((e: unknown) => {
      if (e instanceof ApiError && e.status === 404) setOff(true);      // switched off on this system
      else setError(e instanceof Error ? e.message : "Could not load");
    });
  }, []);
  return (
    <>
      <div className="hero"><div>
        <h1>My Health Records</h1>
        <p>Which of your records clinics hold in their own systems, so you know whom to ask. Zoiko Access does not hold the records themselves, and cannot show or download them.</p>
      </div></div>
      {off && <div className="notice" role="status">This list is not switched on for your clinic yet.</div>}
      {error && <div className="error" role="alert">{error}</div>}
      {!off && (
        <div className="grid">
          <Card title={`Records held for you (${rows?.length ?? 0})`}>
            {rows === null ? <div className="empty">{error ? "Could not load the list." : "Loading…"}</div>
              : rows.length === 0 ? <div className="empty">No clinic has listed a record for you yet.</div>
              : rows.map((r) => (
                <div key={r.id} className="appt">
                  <div className="info">
                    <b>{KIND[r.kind] ?? r.kind} <small>dated {r.refers_to}</small></b>
                    <small>Held by {r.organization.name || "your clinic"} in its system “{r.system}”. Ask the clinic for a copy.</small>
                  </div>
                  {r.status === "withdrawn" ? <Pill kind="warn">No longer listed as held</Pill> : <Pill kind="ok">Held</Pill>}
                </div>
              ))}
          </Card>
        </div>
      )}
    </>
  );
}
