import { useCallback, useEffect, useState } from "react";
import { ApiError, createBooking, getSlots, joinWaitlist, searchServices, type Booking, type CategoryOption, type Me, type Service, type ServiceFilters, type ProfileRow, type Slot, type WaitEntry } from "../api";
import type { PageId } from "../App";
import { dayKey, dayLabel, timeLabel, whenLabel } from "../format";
import { Card, Pill, VerifiedMark } from "../ui";

const isWaiting = (list: WaitEntry[], s: Service) => list.some((w) => w.service.id === s.id);

/** Hours as a patient would say them: under an hour, a few hours, or days. */
function usuallyText(h: number): string {
  if (h < 1) return "an hour";
  if (h < 48) return `${Math.max(1, Math.round(h))} hour${Math.round(h) === 1 ? "" : "s"}`;
  return `${Math.round(h / 24)} days`;
}

export function FindCare({ me, waiting, profiles, onBooked, goTo, initialQuery = "" }: {
  initialQuery?: string;
  me: Me; waiting: WaitEntry[]; profiles: ProfileRow[]; onBooked: () => Promise<void>; goTo: (p: PageId) => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [services, setServices] = useState<Service[] | null>(null);
  const [service, setService] = useState<Service | null>(null);
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [slot, setSlot] = useState<Slot | null>(null);
  // Book for yourself by default (the list is sorted by id, so the first entry may be a child).
  const own = profiles.find((x) => x.own)?.person ?? me.persons[0] ?? "";
  const [chosen, setChosen] = useState("");
  const person = chosen || own;
  const setPerson = setChosen;
  const labelOf = (id: string, i: number) =>
    id === own ? "Myself" : profiles.find((x) => x.person === id)?.name || `Family member ${i}`;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<Booking | null>(null);

  const [category, setCategory] = useState("");
  const [mode, setMode] = useState<ServiceFilters["mode"]>("");
  const [available, setAvailable] = useState(false);
  const [sort, setSort] = useState<NonNullable<ServiceFilters["sort"]>>("name");
  const [categories, setCategories] = useState<CategoryOption[]>([]);

  const [applied, setApplied] = useState(initialQuery);        // the search text that was actually submitted

  const loadServices = useCallback(async () => {
    try {
      const found = await searchServices({ q: applied, category, mode, available, sort });
      setServices(found.services);
      setCategories(found.categories);
      setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load services"); }
  }, [applied, category, mode, available, sort]);

  // Reload when a filter changes or a search is submitted (typing alone waits for the button).
  useEffect(() => { void loadServices(); }, [loadServices]);

  async function choose(s: Service) {
    setService(s); setSlot(null); setSlots(null); setDone(null); setError("");
    try { setSlots(await getSlots(s.id)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load times"); }
  }

  async function wait(s: Service) {
    setError("");
    try { await joinWaitlist(s.id, person || undefined); await onBooked(); }
    catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? "You are already on that waiting list."
        : e instanceof Error ? e.message : "Could not join the waiting list");
    }
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
        <form onSubmit={(e) => { e.preventDefault(); setApplied(query); }} style={{ display: "flex", gap: 8 }}>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search services"
            aria-label="Search services" style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }} />
          <button className="btn" type="submit">Search</button>
        </form>
      </div>

      <div className="filters" role="group" aria-label="Filter services">
        <label>Specialty
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All</option>
            {categories.map((c) => (
              <option key={c.category} value={c.category}>{c.category.replace(/_/g, " ")} ({c.count})</option>
            ))}
          </select>
        </label>
        <label>How
          <select value={mode} onChange={(e) => setMode(e.target.value as ServiceFilters["mode"])}>
            <option value="">Any</option>
            <option value="in_person">In person</option>
            <option value="video">Video</option>
          </select>
        </label>
        <label>Sort by
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
            <option value="name">Name</option>
            <option value="soonest">Soonest free time</option>
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={available} onChange={(e) => setAvailable(e.target.checked)} />
          Only with free times
        </label>
        {(category || mode || available || sort !== "name" || query || applied) && (
          <button className="btn ghost" onClick={() => { setCategory(""); setMode(""); setAvailable(false); setSort("name"); setQuery(""); setApplied(""); }}>
            Clear
          </button>
        )}
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
              <div key={s.id} className={`service ${service?.id === s.id ? "selected" : ""}`}>
                <h3><button className="service-pick" aria-pressed={service?.id === s.id} onClick={() => void choose(s)}>{s.name}</button></h3>
                <span className="meta">{s.organization.name} - {s.duration_minutes} min - {s.mode === "video" ? "Video" : "In person"}</span>
                <span className="meta">{s.description}</span>
                {s.usual_confirmation_hours != null && (
                  <span className="meta">Typically confirmed in about {usuallyText(s.usual_confirmation_hours)} (the middle time of recent requests)</span>
                )}
                <span className="next">
                  {s.next_slot_local && s.next_slot_zone
                    ? <>Next: <b>{whenLabel(s.next_slot_local, s.next_slot_zone)}</b></>
                    : isWaiting(waiting, s) ? <Pill kind="info">On the waiting list</Pill>
                    : <span><Pill kind="warn">No free times</Pill>{" "}
                        <button className="linklike" style={{ marginTop: 0 }} onClick={() => void wait(s)}>Tell me when one opens</button></span>}
                </span>
              </div>
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
                        {timeLabel(s.local_start)}{s.provider ? <small style={{ display: "block", fontWeight: 400 }}>{s.provider.name} <VerifiedMark on={s.provider.verified} /></small> : null}
                      </button>
                    ))}
                  </div>
                </div>
              ))
            )}

            {slot && (
              <div className="confirm-box">
                <b>Book {service.name}</b>
                <div>{whenLabel(slot.local_start, slot.local_zone)} - {slot.location}{slot.provider ? ` - with ${slot.provider.name} ` : ""}{slot.provider && <VerifiedMark on={slot.provider.verified} />}</div>
                {me.persons.length > 1 && (
                  <label style={{ display: "block", marginTop: 8 }}>For
                    <select value={person} onChange={(e) => setPerson(e.target.value)} style={{ marginLeft: 8 }}>
                      {me.persons.map((p, i) => <option key={p} value={p}>{labelOf(p, i)}</option>)}
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
              <div className="notice" role="status">
                Request sent for {whenLabel(done.local_start, done.local_zone)}. It is not confirmed until the clinic confirms it.{" "}
                <button className="btn ghost" onClick={() => goTo("appointments")}>View appointments</button>
              </div>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
