import { useEffect, useState } from "react";
import { getDirectory, type Directory, type DirectoryDoctor } from "../api";
import { Card, Pill } from "../ui";

function when(local: string): string {
  return new Date(local).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

function Listing({ d, onFind }: { d: DirectoryDoctor; onFind: (q: string) => void }) {
  const check = d.name_check;
  return (
    <div className="appt" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <div className="info">
        <b>{d.name || "A doctor"}</b>{" "}
        {check.state === "verified"
          ? <Pill kind="ok">Name checked</Pill>
          : <Pill kind="info">Name not checked</Pill>}
        <small>{d.organization.name} · {d.services.map((s) => s.name).join(", ")}</small>
        <small>{d.places.map((p) => [p.name, p.address].filter(Boolean).join(", ")).join(" · ")}</small>
        {check.state === "verified" && (
          <small>Name checked on {check.checked_on}: {check.basis}.</small>
        )}
        <small>
          {d.next_free ? `Next free time: ${when(d.next_free.local_start)} (${d.next_free.zone}), ${d.free_times} free in all.` : "No free times."}
          {" "}{d.booking_method === "booking_system"
            ? "The clinic's booking system confirms your booking."
            : "The clinic confirms your booking."}
        </small>
      </div>
      <div className="form-row">
        <button className="btn" onClick={() => onFind(d.services[0]?.name ?? d.name)}>See times and book</button>
      </div>
    </div>
  );
}

/** The provider directory: who you can book with, and what is known about each, with where it comes from. */
export function Doctors({ onFind }: { onFind: (q: string) => void }) {
  const [data, setData] = useState<Directory | null>(null);
  const [text, setText] = useState("");
  const [applied, setApplied] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    getDirectory(applied).then((d) => { setData(d); setError(""); })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load doctors"));
  }, [applied]);
  return (
    <>
      <div className="hero"><div>
        <h1>Find a doctor</h1>
        <p>Doctors with a free time you can book. Search by a doctor's name, a service or a place.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <form className="form-row" onSubmit={(e) => { e.preventDefault(); setApplied(text.trim()); }}>
        <input value={text} maxLength={100} placeholder="Doctor, service or place" aria-label="Search doctors"
          onChange={(e) => setText(e.target.value)} />{" "}
        <button className="btn" type="submit">Search</button>
      </form>
      <div className="grid">
        <Card title={data ? `${data.doctors.length} doctor${data.doctors.length === 1 ? "" : "s"}` : "Doctors"}
          sub={data ? `${data.disclaimer} Times as of ${new Date(data.availability.as_of).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}, from ${data.availability.source}.` : ""}>
          {data === null ? <div className="empty">Loading…</div> : data.doctors.length === 0
            ? <div className="empty">No doctor with a free time matches. Try a different search.</div>
            : data.doctors.map((d) => <Listing key={d.id} d={d} onFind={onFind} />)}
        </Card>
      </div>
    </>
  );
}
