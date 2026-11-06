import type { Booking } from "../api";
import { Card } from "../ui";
import type { PageId } from "../App";

interface Site {
  organization: string;
  name: string;
  address: string;
  phone: string;
}

/** The sites the patient has dealt with, once each, most recent first. */
export function sitesOf(bookings: Booking[]): Site[] {
  const seen = new Map<string, Site>();
  const recent = [...bookings].sort((a, b) => b.created_at.localeCompare(a.created_at));
  for (const b of recent) {
    if (!seen.has(b.location.id)) {
      seen.set(b.location.id, {
        organization: b.organization.name, name: b.location.name,
        address: b.location.address, phone: b.location.phone ?? "",
      });
    }
  }
  return [...seen.values()];
}

export function Help({ bookings, goTo }: { bookings: Booking[]; goTo: (page: PageId) => void }) {
  const sites = sitesOf(bookings);
  return (
    <>
      <div className="hero"><div>
        <h1>Help &amp; Support</h1>
        <p>How to reach the clinics you have booked with, and what to do when you need help now.</p>
      </div></div>
      <div className="grid">
        <Card title="In an emergency">
          <p><b>Zoiko Access is not an emergency service.</b> If you or someone else is in danger or needs urgent medical help, call your local emergency number straight away. Do not wait for a reply in this app.</p>
        </Card>
        <Card title="Your clinics" sub="From your appointments. The clinic can change these details.">
          {sites.length === 0 ? (
            <div className="empty">You have no appointments yet, so there are no clinics to show. <button className="link" onClick={() => goTo("find")}>Find healthcare</button></div>
          ) : sites.map((s) => (
            <div className="appt" key={`${s.organization}-${s.name}`}>
              <div className="info">
                <b>{s.organization}</b>
                <small>{s.name}{s.address ? `, ${s.address}` : ""}</small>
                <small>{s.phone ? <>Phone: <a href={`tel:${s.phone.replace(/[^+0-9]/g, "")}`}>{s.phone}</a></> : "This clinic has not added a phone number yet."}</small>
              </div>
              <button className="btn ghost" onClick={() => goTo("messages")}>Send a message</button>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
