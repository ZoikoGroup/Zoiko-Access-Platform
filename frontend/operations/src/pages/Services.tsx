import { useCallback, useEffect, useState } from "react";
import {
  addSlots, createLocation, createService, updateLocationContact, getDirectory, listLocations, listStaffServices,
  listFreeTimes, setServiceStatus, withdrawSlot, withdrawSlots, type Staff,
} from "../api";
import type { Directory, FreeTime, LocationRow, ServiceRow } from "../types";
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
  const [loc, setLoc] = useState({ name: "", zone: "Asia/Kolkata", address: "", phone: "" });
  const [editing, setEditing] = useState<{ id: string; phone: string; address: string } | null>(null);
  const [slotFor, setSlotFor] = useState("");
  const [slots, setSlots] = useState({ location: "", start: defaultStart(), count: "8", provider: "", days: "1", weekdaysOnly: false });
  const [doctors, setDoctors] = useState<Directory["staff"]>([]);
  const [away, setAway] = useState({ first: "", last: "", provider: "" });          // "Remove free times"
  const [awayCount, setAwayCount] = useState<number | null>(null);                  // how many would go
  const [times, setTimes] = useState<FreeTime[]>([]);                              // the chosen service's free times
  const total = (Number(slots.count) || 0) * (Number(slots.days) || 0);   // how many times the form will make

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
  // the open service's free times: reloaded after every change (`services` is reloaded by each action)
  useEffect(() => {
    if (!slotFor) { setTimes([]); return; }
    listFreeTimes(slotFor).then(setTimes).catch(() => setTimes([]));
  }, [slotFor, services]);

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
          <Table headers={["Name", "Time zone", "Address", "Phone", ""]} empty="No locations yet. Add one first."
            rows={locations.map((l) => [l.name, l.time_zone, l.address || "-", l.phone || <Pill key="p" kind="warn">no phone</Pill>,
              <button key="e" className="btn ghost" onClick={() => setEditing({ id: l.id, phone: l.phone, address: l.address })}>Edit contact</button>])} />
          {editing && (
            <div className="form-row">
              <input aria-label="Phone" placeholder="Phone, e.g. +91 98765 43210" value={editing.phone} onChange={(e) => setEditing({ ...editing, phone: e.target.value })} />
              <input aria-label="Address" placeholder="Address" value={editing.address} onChange={(e) => setEditing({ ...editing, address: e.target.value })} />
              <button className="btn" disabled={busy}
                onClick={() => void run(() => updateLocationContact(editing.id, editing.phone, editing.address).then(() => setEditing(null)), "Contact details saved")}>Save</button>
              <button className="btn ghost" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          )}
          <div className="form-row">
            <input aria-label="Location name" placeholder="Name, e.g. Main Block" value={loc.name} onChange={(e) => setLoc({ ...loc, name: e.target.value })} />
            <input aria-label="Time zone" list="zones" value={loc.zone} onChange={(e) => setLoc({ ...loc, zone: e.target.value })} />
            <datalist id="zones">{ZONES.map((z) => <option key={z} value={z} />)}</datalist>
            <input aria-label="Address" placeholder="Address (optional)" value={loc.address} onChange={(e) => setLoc({ ...loc, address: e.target.value })} />
            <input aria-label="New location phone" placeholder="Phone (optional)" value={loc.phone} onChange={(e) => setLoc({ ...loc, phone: e.target.value })} />
            <button className="btn" disabled={busy || !loc.name.trim()}
              onClick={() => void run(() => createLocation({ organization: org, name: loc.name, time_zone: loc.zone, address: loc.address, phone: loc.phone }).then(() => setLoc({ ...loc, name: "", address: "", phone: "" })), "Location added")}>
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
            <Table headers={["Service", "Category", "Length", "Mode", "Status", "Next free time", "Waiting", "Actions"]}
              empty="No services yet. Add one above."
              rows={services.map((s) => [
                s.name, s.category, `${s.duration_minutes} min`, s.mode === "video" ? "Video" : "In person",
                <Pill key="s" kind={s.status === "active" ? "ok" : "bad"}>{s.status === "active" ? "Visible" : "Hidden"}</Pill>,
                s.next_slot_local ? `${s.next_slot_local.replace("T", " ").slice(0, 16)} (${s.next_slot_zone})` : <Pill key="n" kind="warn">no free times</Pill>,
                s.waiting ? <Pill key="w" kind="info">{s.waiting} waiting</Pill> : "",
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
              <input aria-label="Repeat for how many days" type="number" min={1} max={31} style={{ maxWidth: 110, flex: "0 0 110px" }} value={slots.days} onChange={(e) => setSlots({ ...slots, days: e.target.value })} />
              <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input type="checkbox" checked={slots.weekdaysOnly} onChange={(e) => setSlots({ ...slots, weekdaysOnly: e.target.checked })} />
                Skip Saturday and Sunday
              </label>
              <button className="btn" disabled={busy || !slots.location || total < 1 || total > 400}
                onClick={() => void run(() => addSlots(slotFor, { location: slots.location, first_local: `${slots.start}:00`, count: Number(slots.count), days: Number(slots.days), weekdays_only: slots.weekdaysOnly, ...(slots.provider ? { provider: slots.provider } : {}) }).then(() => setSlotFor("")), "Times added")}>
                {total > 0 && total <= 400 ? `Create ${total} time${total === 1 ? "" : "s"}` : "Create times"}
              </button>
              {total > 400 && <div className="error" style={{ flexBasis: "100%" }}>That is {total} times: at most 400 at once. Use fewer days or fewer times a day.</div>}
              <div style={{ flexBasis: "100%", borderTop: "1px solid var(--line)", paddingTop: 12, marginTop: 4 }}>
                <b>Next free times</b>
                <div className="sub">Remove one time (for example a single cancelled slot). A time with a booking is not listed.</div>
                <Table headers={["When (clinic clock)", "Doctor", "Place", ""]} empty="No free times."
                  rows={times.slice(0, 15).map((t) => [
                    `${t.local_start.replace("T", " ").slice(0, 16)} (${t.local_zone})`, t.provider?.name ?? "any", t.location,
                    <button key="r" className="btn ghost" disabled={busy}
                      onClick={() => void run(() => withdrawSlot(t.id), "Time removed")}>Remove</button>,
                  ])} />
                {times.length > 15 && <div className="sub">Showing the next 15 of {times.length}.</div>}
              </div>
              <div style={{ flexBasis: "100%", borderTop: "1px solid var(--line)", paddingTop: 12, marginTop: 4 }}>
                <b>Remove free times</b>
                <div className="sub">Takes free times off the calendar for these days (a holiday, a doctor away). Times that already have a booking are never touched: cancel or move those one by one. You can bring a removed time back by creating it again.</div>
                <div className="form-row">
                  <input aria-label="From day" type="date" value={away.first} onChange={(e) => { setAway({ ...away, first: e.target.value }); setAwayCount(null); }} />
                  <input aria-label="To day (included)" type="date" value={away.last} onChange={(e) => { setAway({ ...away, last: e.target.value }); setAwayCount(null); }} />
                  <select aria-label="Only this doctor" value={away.provider}
                    onChange={(e) => { setAway({ ...away, provider: e.target.value }); setAwayCount(null); }}
                    style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }}>
                    <option value="">Every doctor</option>
                    {doctors.map((d) => <option key={d.id} value={d.id}>{d.login}</option>)}
                  </select>
                  {awayCount === null ? (
                    <button className="btn ghost" disabled={busy || !away.first || !away.last}
                      onClick={() => void run(async () => { setAwayCount((await withdrawSlots(slotFor, { first_day: away.first, last_day: away.last, ...(away.provider ? { provider: away.provider } : {}), dry_run: true })).count); }, "")}>
                      Count them
                    </button>
                  ) : (
                    <>
                      <span>{awayCount === 0 ? "No free times in those days." : `${awayCount} free time${awayCount === 1 ? "" : "s"} will be removed.`}</span>
                      {awayCount > 0 && (
                        <button className="btn" disabled={busy}
                          onClick={() => void run(() => withdrawSlots(slotFor, { first_day: away.first, last_day: away.last, ...(away.provider ? { provider: away.provider } : {}) }).then(() => { setAwayCount(null); setAway({ first: "", last: "", provider: "" }); }), "Times removed")}>
                          Yes, remove them
                        </button>
                      )}
                      <button className="btn ghost" onClick={() => setAwayCount(null)}>Change</button>
                    </>
                  )}
                </div>
              </div>
              <div className="sub" style={{ flexBasis: "100%" }}>
                The time is the clinic's own clock at that location. Times that do not exist (clock changes) are refused. With more than one day, the same times are made each day, starting from the day above; if any one of them clashes with an existing time, none are made.
              </div>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
