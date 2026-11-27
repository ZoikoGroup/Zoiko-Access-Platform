import { useCallback, useEffect, useState } from "react";
import { answerCrossMatch, getCrossMatching, setCrossMatching, type CrossCase } from "../api";
import { Card, Pill, type PillKind } from "../ui";

const STATE: Record<CrossCase["state"], { text: string; kind: PillKind }> = {
  open: { text: "Waiting for both organizations", kind: "warn" },
  confirmed: { text: "Both say it is the same person", kind: "ok" },
  not_same: { text: "Not the same person", kind: "info" },
};
const FACTOR: Record<string, string> = { birth_date: "date of birth", surname: "surname", phone: "phone" };

/** Possible matches between this clinic's patients and another organization's (DEV-363). */
export function CrossMatches() {
  const [data, setData] = useState<{ active: boolean; can_set: boolean; cases: CrossCase[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await getCrossMatching()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function run(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That did not work"); }
    finally { setBusy(false); }
  }
  return (
    <Card title="Matches with other organizations"
      sub="Only fingerprints of a patient's date of birth with their surname, and of their phone, are compared: never names or details. Nothing is merged. Each organization answers for its own patient; only when both say “same” does either learn who the other is.">
      {error && <div className="error" role="alert">{error}</div>}
      {data === null ? <div className="empty">{error ? "Could not load." : "Loading…"}</div> : (
        <>
          <div className="row">
            <Pill kind={data.active ? "ok" : "info"}>{data.active ? "Taking part" : "Not taking part"}</Pill>
            {data.can_set && (
              <button className="btn ghost" disabled={busy} onClick={() => void run(() => setCrossMatching(!data.active))}>
                {data.active ? "Stop taking part (removes our fingerprints)" : "Take part"}
              </button>
            )}
            {!data.can_set && <small>Only your clinic administrator can change this.</small>}
          </div>
          {data.cases.length === 0 ? <div className="empty">No possible matches with other organizations.</div> : data.cases.map((c) => (
            <div key={c.id} className="appt">
              <div className="info">
                <b>Your patient {c.patient.initials || "?"}{c.patient.birth_year ? `, born ${c.patient.birth_year}` : ""} <Pill kind={STATE[c.state].kind}>{STATE[c.state].text}</Pill></b>
                <small>The same {c.factors.map((f) => FACTOR[f] ?? f).join(", ")}.{c.other_organization ? ` Also known at ${c.other_organization}.` : ""}</small>
                <small>{c.my_answer ? `You said: ${c.my_answer === "same" ? "the same person" : "not the same person"}.` : "You have not answered."} {c.their_answer_given ? "The other organization has answered." : "The other organization has not answered yet."}</small>
              </div>
              {c.state === "open" && !c.my_answer && (
                <div className="row">
                  <button className="btn" disabled={busy} onClick={() => void run(() => answerCrossMatch(c.id, "same"))}>Same person</button>
                  <button className="btn ghost" disabled={busy} onClick={() => void run(() => answerCrossMatch(c.id, "not_same"))}>Not the same</button>
                </div>
              )}
            </div>
          ))}
        </>
      )}
    </Card>
  );
}
