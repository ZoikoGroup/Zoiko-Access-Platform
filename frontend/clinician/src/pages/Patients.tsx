import { getPatients } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Patient, Pill, Table } from "../ui";

const WHY: Record<string, string> = {
  appointment: "an appointment", referral: "a referral", care_team: "your care team",
};

export function Patients() {
  const result = useLoad(getPatients);
  return (
    <Gate result={result}>
      {(rows) => (
        <>
          <div className="hero"><div>
            <h1>My patients</h1>
            <p>People you have a care relationship with. You can see a patient only while that relationship is active. A patient shows by name once the clinic or the patient has entered their details.</p>
          </div></div>
          <div className="grid">
            <Card title={`${rows.length} patient${rows.length === 1 ? "" : "s"}`}>
              <Table headers={["Patient", "Why you can see them", "Since", "Appointments with you"]}
                empty="No patients yet. A patient appears here after a clinic confirms their booking with you."
                rows={rows.map((p) => [
                  <Patient key="i" name={p.patient_name} id={p.id} />,
                  <Pill key="b" kind="info">{WHY[p.basis] ?? p.basis}</Pill>,
                  p.since.slice(0, 10), p.appointments,
                ])} />
            </Card>
          </div>
        </>
      )}
    </Gate>
  );
}
