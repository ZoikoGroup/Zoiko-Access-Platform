import { useCallback, useEffect, useState } from "react";
import { ApiError, createBooking, getServices, getSlots, type Booking, type Me, type Service, type Slot } from "../api";
import type { PageId } from "../App";
import { dayKey, dayLabel, timeLabel, whenLabel } from "../format";
import { Card, Pill } from "../ui";

export function FindCare({ me, onBooked, goTo }: {
  me: Me; onBooked: () => Promise<void>; goTo: (p: PageId) => void;
}) {
  const [query, setQuery] = useState("");
  const [services, setServices] = useState<Service[] | null>(null);
  const [service, setService] = useState<Service | null>(null);
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [slot, setSlot] = useState<Slot | null>(null);
  const [person, setPerson] = useState(me.persons[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<Booking | null>(null);

  const loadServices = useCallback(async (q: string) => {
    try { setServices(await getServices(q)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load services"); }
  }, []);

  useEffect(() => { void loadServices(""); }, [loadServices]);

  async function choose(s: Service) {
    setService(s); setSlot(null); setSlots(null); setDone(null); setError("");
    try { setSlots(await getSlots(s.id)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load times"); }
  }

  async function book() {
    if (!slot || !service) return;
    setBusy(true); setError("");
    try {
      const booking = await createBooking(slot.id, person || undefined);
      setDone(booking); setSlot(null);
      await onBooked();
      setSlots(await getSlots(service.id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError("Someone just took that time. Please pick another.");
        setSlot(null);
        setSlots(await getSlots(service.id));
      } else if (e instanceof ApiError && e.status === 403) {
        setError("You are not allowed to book for this person.");
      } else {
        setError(e instanceof Error ? e.message : "Booking failed");
      }
    } finally { setBusy(false); }
  }

  const byDay = new Map<string, Slot[]>();
  for (const s of slots ?? []) byDay.set(dayKey(s.local_start), [...(byDay.get(dayKey(s.local_start)) ?? []), s]);

  return (
    <>
      <div className="hero">
        <div><h1>Find Care</h1><p>Choose a service, pick a free time, and book. The clinic then confirms.</p></div>
        <span className="spacer" />
        <form onSubmit={(e) => { e.preventDefault(); void loadServices(query); }} style={{ display: "flex", gap: 8 }}>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search services"
            aria-label="Search services" style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }} />
          <button className="btn" type="submit">Search</button>
        </form>
      </div>

      {me.persons.length === 0 && (
        <div className="notice">Your login is not linked to a patient record yet, so you cannot book. Ask the clinic to link it.</div>
      )}
      {error && <div className="error">{error}</div>}

      <div className="grid">
        {services === null ? <div className="notice">Loading services…</div> : services.length === 0 ? (
          <Card title="No services found"><div className="empty">Try a different search, or the clinic has not listed services yet.</div></Card>
        ) : (
          <div className="service-grid">
            {services.map((s) => (
              <button key={s.id} className={`service ${service?.id === s.id ? "selected" : ""}`} onClick={() => void choose(s)}>
                <h3>{s.name}</h3>
                <span className="meta">{s.organization.name} - {s.duration_minutes} min - {s.mode === "video" ? "Video" : "In person"}</span>
                <span className="meta">{s.description}</span>
                <span className="next">
                  {s.next_slot_local && s.next_slot_zone
                    ? <>Next: <b>{whenLabel(s.next_slot_local, s.next_slot_zone)}</b></>
                    : <Pill kind="warn">No free times</Pill>}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {service && (
        <div className="grid">
          <Card title={`Free times for ${service.name}`} sub={service.organization.name}>
            {slots === null ? <div className="empty">Loading times…</div> : slots.length === 0 ? (
              <div className="empty">There are no free times right now.</div>
            ) : (
              [...byDay.entries()].map(([day, list]) => (
                <div key={day}>
                  <div className="slot-day">{dayLabel(list[0].local_start)} - {list[0].location} ({list[0].local_zone})</div>
                  <div className="slots">
                    {list.map((s) => (
                      <button key={s.id} className={`slot ${slot?.id === s.id ? "chosen" : ""}`} onClick={() => { setSlot(s); setDone(null); }}>
                        {timeLabel(s.local_start)}{s.provider ? <small style={{ display: "block", fontWeight: 400 }}>{s.provider.name}</small> : null}
                      </button>
                    ))}
                  </div>
                </div>
              ))
            )}

            {slot && (
              <div className="confirm-box">
                <b>Book {service.name}</b>
                <div>{whenLabel(slot.local_start, slot.local_zone)} - {slot.location}{slot.provider ? ` - with ${slot.provider.name}` : ""}</div>
                {me.persons.length > 1 && (
                  <label style={{ display: "block", marginTop: 8 }}>For
                    <select value={person} onChange={(e) => setPerson(e.target.value)} style={{ marginLeft: 8 }}>
                      {me.persons.map((p, i) => <option key={p} value={p}>{i === 0 ? "Myself" : `Family member ${i}`}</option>)}
                    </select>
                  </label>
                )}
                <div className="row">
                  <button className="btn" disabled={busy || me.persons.length === 0} onClick={() => void book()}>
                    {busy ? "Booking…" : "Confirm booking"}
                  </button>
                  <button className="btn ghost" onClick={() => setSlot(null)}>Choose another time</button>
                </div>
              </div>
            )}

            {done && (
              <div className="success">
                Booked for {whenLabel(done.local_start, done.local_zone)}. The clinic will confirm it.{" "}
                <button className="btn ghost" onClick={() => goTo("appointments")}>View appointments</button>
              </div>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
