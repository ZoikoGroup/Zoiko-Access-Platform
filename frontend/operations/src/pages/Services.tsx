import { useCallback, useEffect, useState } from "react";
import {
  addSlots, createLocation, createService, getDirectory, listLocations, listStaffServices,
  setServiceStatus, type Staff,
} from "../api";
import type { Directory, LocationRow, ServiceRow } from "../types";
import { Card, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

const ZONES = ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "Europe/Berlin",
  "America/New_York", "America/Los_Angeles", "Australia/Sydney", "UTC"];

function defaultStart(): string {
  const d = new Date(Date.now() + 3 * 86_400_000);
  d.setHours(9, 0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T09:00`;
}

export function Services({ staff }: { staff: Staff | null }) {
  const [services, setServices] = useState<ServiceRow[] | null>(null);
  const [locations, setLocations] = useState<LocationRow[]>([]);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const [svc, setSvc] = useState({ name: "", category: "", duration: "30", mode: "in_person", description: "" });
  const [loc, setLoc] = useState({ name: "", zone: "Asia/Kolkata", address: "" });
  const [slotFor, setSlotFor] = useState("");
  const [slots, setSlots] = useState({ location: "", start: defaultStart(), count: "8", provider: "" });
  const [doctors, setDoctors] = useState<Directory["staff"]>([]);

  const load = useCallback(async () => {
    if (!staff) return;
    try {
      const [s, l, dir] = await Promise.all([listStaffServices(), listLocations(), getDirectory()]);
      setServices(s); setLocations(l); setError("");
      setDoctors(dir.staff.filter((m) => m.status === "active" && m.type === "clinician"
        && m.roles.some((r) => r.role === "R03" && r.status === "active" && r.organization_id === staff.organization)));
      setSlots((cur) => (cur.location || !l[0] ? cur : { ...cur, location: l[0].id }));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load the catalog"); }
  }, [staff]);

  useEffect(() => { setServices(null); void load(); }, [load]);

  async function run(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setNote("");
    try { await work(); setNote(success); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }

  if (!staff) return <NoStaff />;
  const org = staff.organization;

  return (
    <>
      <div className="hero"><div>
        <h1>Services</h1>
        <p>What {staff.organizationName} offers, where, and when it can be booked. Patients see active services with free times.</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      {note && <div className="notice" style={{ background: "var(--ok-bg)", color: "var(--ok)" }}>{note}</div>}

      <div className="grid cols-2">
        <Card title="Locations" sub="A place has a time zone: slot times are the clinic's wall-clock there.">
          <Table headers={["Name", "Time zone", "Address"]} empty="No locations yet. Add one first."
            rows={locations.map((l) => [l.name, l.time_zone, l.address || "-"])} />
          <div className="form-row">
            <input aria-label="Location name" placeholder="Name, e.g. Main Block" value={loc.name} onChange={(e) => setLoc({ ...loc, name: e.target.value })} />
            <input aria-label="Time zone" list="zones" value={loc.zone} onChange={(e) => setLoc({ ...loc, zone: e.target.value })} />
            <datalist id="zones">{ZONES.map((z) => <option key={z} value={z} />)}</datalist>
            <input aria-label="Address" placeholder="Address (optional)" value={loc.address} onChange={(e) => setLoc({ ...loc, address: e.target.value })} />
            <button className="btn" disabled={busy || !loc.name.trim()}
              onClick={() => void run(() => createLocation({ organization: org, name: loc.name, time_zone: loc.zone, address: loc.address }).then(() => setLoc({ ...loc, name: "", address: "" })), "Location added")}>
              Add location
            </button>
          </div>
        </Card>

        <Card title="Add a service">
          <div className="form-row" style={{ marginTop: 0 }}>
            <input aria-label="Service name" placeholder="Name, e.g. Dermatology" value={svc.name} onChange={(e) => setSvc({ ...svc, name: e.target.value })} />
            <input aria-label="Category" placeholder="category_code, e.g. dermatology" value={svc.category} onChange={(e) => setSvc({ ...svc, category: e.target.value })} />
            <input aria-label="Minutes" type="number" min={5} max={480} style={{ maxWidth: 110, flex: "0 0 110px" }} value={svc.duration} onChange={(e) => setSvc({ ...svc, duration: e.target.value })} />
            <select aria-label="Mode" value={svc.mode} onChange={(e) => setSvc({ ...svc, mode: e.target.value })}
              style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }}>
              <option value="in_person">In person</option><option value="video">Video</option>
            </select>
            <input aria-label="Description" placeholder="Short description" value={svc.description} onChange={(e) => setSvc({ ...svc, description: e.target.value })} />
            <button className="btn" disabled={busy || !svc.name.trim() || !svc.category.trim()}
              onClick={() => void run(() => createService({ organization: org, name: svc.name, category: svc.category, duration_minutes: Number(svc.duration), mode: svc.mode, description: svc.description }).then(() => setSvc({ ...svc, name: "", category: "", description: "" })), "Service added")}>
              Add service
            </button>
          </div>
        </Card>
      </div>

      <div className="grid">
        <Card title={`${services?.length ?? 0} service${services?.length === 1 ? "" : "s"}`}>
          {services === null ? <div className="empty">Loading…</div> : (
            <Table headers={["Service", "Category", "Length", "Mode", "Status", "Next free time", "Actions"]}
              empty="No services yet. Add one above."
              rows={services.map((s) => [
                s.name, s.category, `${s.duration_minutes} min`, s.mode === "video" ? "Video" : "In person",
                <Pill key="s" kind={s.status === "active" ? "ok" : "bad"}>{s.status === "active" ? "Visible" : "Hidden"}</Pill>,
                s.next_slot_local ? `${s.next_slot_local.replace("T", " ").slice(0, 16)} (${s.next_slot_zone})` : <Pill key="n" kind="warn">no free times</Pill>,
                <span key="a">
                  <button className="btn ghost" disabled={busy}
                    onClick={() => void run(() => setServiceStatus(s.id, s.status === "active" ? "inactive" : "active"), s.status === "active" ? "Service hidden from patients" : "Service visible to patients")}>
                    {s.status === "active" ? "Hide" : "Show"}
                  </button>{" "}
                  <button className="btn ghost" onClick={() => setSlotFor(slotFor === s.id ? "" : s.id)}>Add times</button>
                </span>,
              ])} />
          )}
          {slotFor && (
            <div className="form-row">
              <select aria-label="Location for the new times" value={slots.location} onChange={(e) => setSlots({ ...slots, location: e.target.value })}
                style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }}>
                {locations.length === 0 && <option value="">Add a location first</option>}
                {locations.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.time_zone})</option>)}
              </select>
              <select aria-label="Doctor for the new times" value={slots.provider}
                onChange={(e) => setSlots({ ...slots, provider: e.target.value })}
                style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }}>
                <option value="">No specific doctor</option>
                {doctors.map((d) => <option key={d.id} value={d.id}>{d.login}</option>)}
              </select>
              <input aria-label="First time (clinic clock)" type="datetime-local" value={slots.start} onChange={(e) => setSlots({ ...slots, start: e.target.value })} />
              <input aria-label="How many back-to-back times" type="number" min={1} max={200} style={{ maxWidth: 110, flex: "0 0 110px" }} value={slots.count} onChange={(e) => setSlots({ ...slots, count: e.target.value })} />
              <button className="btn" disabled={busy || !slots.location}
                onClick={() => void run(() => addSlots(slotFor, { location: slots.location, first_local: `${slots.start}:00`, count: Number(slots.count), ...(slots.provider ? { provider: slots.provider } : {}) }).then(() => setSlotFor("")), "Times added")}>
                Create times
              </button>
              <div className="sub" style={{ flexBasis: "100%" }}>
                The time is the clinic's own clock at that location. Times that do not exist (clock changes) are refused.
              </div>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
