import { useEffect, useState } from "react";
import { getAccessLog, type AccessEntry } from "../api";
import { Card, Pill } from "../ui";

// What each recorded action means, in plain words.
const WHAT: Record<string, string> = {
  "read:person": "Looked at your details",
  "write:person": "Changed your details",
  "read:booking": "Looked at your appointments",
  "write:booking": "Changed an appointment",
  "read:referral": "Looked at your referrals",
  "write:referral": "Changed a referral",
  "read:coverage": "Looked at your insurance",
  "write:coverage": "Changed your insurance",
  "read:comms": "Looked at your notifications",
  "read:evidence": "Looked at the access log",
};

const WHY: Record<string, string> = {
  DIRECT_CARE: "to treat you",
  OPERATIONS: "to run the clinic",
  CONTINUITY_OF_CARE: "to keep your care going",
  SELF_SERVICE: "you",
  PAYMENT_COVERAGE: "for payment and coverage",
  SUPPORT: "support",
};

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function Privacy() {
  const [entries, setEntries] = useState<AccessEntry[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getAccessLog().then(setEntries).catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, []);

  return (
    <>
      <div className="hero"><div>
        <h1>Privacy &amp; Consent</h1>
        <p>Every time someone else looks at your information, or tries to, it is recorded. This is the list, newest first. It shows who, when and why, never what they saw.</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <Card title={`Who looked at my information${entries ? ` (${entries.length})` : ""}`}
          sub="Your own visits are not listed. A blocked attempt means the system refused it.">
          {entries === null ? <div className="empty">Loading…</div> : entries.length === 0 ? (
            <div className="empty">Nobody else has looked at your information yet.</div>
          ) : entries.map((e, i) => (
            <div className="appt" key={`${e.at}-${i}`}>
              <div className="info">
                <b>{e.who}</b>
                <small>{WHAT[e.operation] ?? e.operation}{WHY[e.purpose] ? ` (${WHY[e.purpose]})` : ""}</small>
                <small>{when(e.at)}</small>
              </div>
              <Pill kind={e.allowed ? "info" : "bad"}>{e.allowed ? "Allowed" : "Blocked"}</Pill>
            </div>
          ))}
        </Card>
        <Card title="Consent choices">
          <div className="empty">Consent settings (choosing who may see what) are not built yet. Today, access follows the roles people hold and the care relationships they have with you.</div>
        </Card>
      </div>
    </>
  );
}
