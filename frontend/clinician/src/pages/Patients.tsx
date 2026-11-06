import { useState } from "react";
import { getPatients } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Patient, Pill, Table } from "../ui";

const WHY: Record<string, string> = {
  appointment: "an appointment", referral: "a referral", care_team: "your care team",
};

export function Patients({ initialSearch = "" }: { initialSearch?: string }) {
  const [search, setSearch] = useState(initialSearch);
  const result = useLoad(getPatients);
  return (
    <Gate result={result}>
      {(all) => {
        const word = search.trim().toLowerCase();
        const rows = word
          ? all.filter((p) => (p.patient_name ?? "").toLowerCase().includes(word) || p.id.toLowerCase().startsWith(word))
          : all;
        return (
        <>
          <div className="hero"><div>
            <h1>My patients</h1>
            <p>People you have a care relationship with. You can see a patient only while that relationship is active. A patient shows by name once the clinic or the patient has entered their details.</p>
          </div>
          <span className="spacer" />
          <input aria-label="Search my patients by name" placeholder="Search by name" value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)", minWidth: 220 }} />
          </div>
          <div className="grid">
            <Card title={word ? `${rows.length} of ${all.length} patients` : `${all.length} patient${all.length === 1 ? "" : "s"}`}>
              <Table headers={["Patient", "Why you can see them", "Since", "Appointments with you"]}
                empty={word ? "No patient of yours matches that name." : "No patients yet. A patient appears here after a clinic confirms their booking with you."}
                rows={rows.map((p) => [
                  <Patient key="i" name={p.patient_name} id={p.id} />,
                  <Pill key="b" kind="info">{WHY[p.basis] ?? p.basis}</Pill>,
                  p.since.slice(0, 10), p.appointments,
                ])} />
            </Card>
          </div>
        </>
        );
      }}
    </Gate>
  );
}
