import { useCallback, useEffect, useState } from "react";
import { PRIVILEGE_STATE, QualificationForm, QualificationRow, type Privilege, type Qualification } from "@shared/Qualifications";
import { addQualification, getMyCredentials } from "../api";
import { Card, Pill } from "../ui";

/** The doctor's own qualifications and what the clinic lets them do. */
export function Credentials() {
  const [data, setData] = useState<{ qualifications: Qualification[]; privileges: Privilege[] } | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await getMyCredentials()); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <>
      <div className="hero"><div>
        <h1>My qualifications</h1>
        <p>Write down what you hold. A clinic administrator records that they checked it; only then do patients see it. A checked entry is not edited: ask for it to be withdrawn and add it again.</p>
      </div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="grid">
        <Card title={`Qualifications (${data?.qualifications.length ?? 0})`}>
          {data === null ? <div className="empty">{error ? "Could not load." : "Loading…"}</div>
            : data.qualifications.length === 0 ? <div className="empty">Nothing written down yet.</div>
            : data.qualifications.map((q) => <QualificationRow key={q.id} q={q} />)}
          <QualificationForm onAdd={async (q) => { await addQualification(q); await load(); }} />
        </Card>
        <Card title="What your clinic lets you do" sub="Recorded by your clinic's administrator.">
          {data === null ? <div className="empty">{error ? "Could not load." : "Loading…"}</div>
            : data.privileges.length === 0 ? <div className="empty">Nothing recorded yet.</div>
            : data.privileges.map((p) => (
              <div key={p.id} className="appt"><div className="info">
                <b>{p.service_name} <Pill kind={PRIVILEGE_STATE[p.state].kind}>{PRIVILEGE_STATE[p.state].text}</Pill></b>
                <small>From {p.valid_from}{p.valid_until ? ` until ${p.valid_until}` : ""}{p.note ? ` · ${p.note}` : ""}</small>
                {p.withdrawn_reason && <small>Withdrawn: {p.withdrawn_reason}</small>}
              </div></div>
            ))}
        </Card>
      </div>
    </>
  );
}
