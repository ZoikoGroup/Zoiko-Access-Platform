import { useEffect, useState } from "react";
import { getCareTeam, type CareTeamPerson } from "../api";
import { Card, Pill } from "../ui";

const WHY: Record<string, string> = {
  appointment: "You have an appointment with them",
  care_team: "Assigned to your care team",
  referral: "Through a referral",
};

export function CareTeam() {
  const [people, setPeople] = useState<CareTeamPerson[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getCareTeam().then(setPeople).catch((e) => setError(e instanceof Error ? e.message : "Could not load"));
  }, []);

  const team = people?.flatMap((p) => p.team) ?? [];
  return (
    <>
      <div className="hero"><div>
        <h1>My Care Team</h1>
        <p>The doctors who can see your information right now, and why. A doctor appears here when the clinic confirms your appointment, and stops being listed when that access runs out. Doctors are shown by their login: names are not stored for them yet.</p>
      </div></div>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <Card title={`Treating you now (${team.length})`}>
          {people === null ? <div className="empty">Loading…</div> : team.length === 0 ? (
            <div className="empty">Nobody yet. A doctor joins your care team when a clinic confirms an appointment with them.</div>
          ) : team.map((m, i) => (
            <div className="appt" key={`${m.clinician}-${i}`}>
              <div className="info">
                <b>{m.clinician || "A doctor"}</b>
                <small>{WHY[m.basis] ?? m.basis}</small>
                <small>Since {m.since.slice(0, 10)}{m.until ? `, until ${m.until.slice(0, 10)}` : ""}</small>
              </div>
              <Pill kind="ok">Can see your record</Pill>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
