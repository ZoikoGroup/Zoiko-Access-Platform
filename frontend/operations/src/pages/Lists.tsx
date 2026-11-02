import { useState } from "react";
import { getDirectory, getPatients } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Patient, Pill, Table } from "../ui";
import { PatientCoverage } from "./PatientCoverage";
import { PatientDetails } from "./PatientDetails";

export function Patients() {
  const [version, setVersion] = useState(0);        // a new number reloads the list
  const [editing, setEditing] = useState("");
  const [search, setSearch] = useState("");
  const result = useLoad(getPatients, 10_000, version);
  return (
    <Gate result={result}>
      {(all) => {
        const word = search.trim().toLowerCase();
        const patients = word
          ? all.filter((p) => (p.patient_name ?? "").toLowerCase().includes(word) || p.id.toLowerCase().startsWith(word))
          : all;
        return (
        <>
          <div className="hero"><div>
            <h1>Patients</h1>
            <p>Patient records and who can reach each one. A patient shows by name once their details are entered (encrypted); emails are never shown.</p>
          </div>
          <span className="spacer" />
          <input aria-label="Search patients by name" placeholder="Search by name" value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ padding: "9px 12px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)", minWidth: 220 }} />
          </div>
          <div className="grid">
            <Card title={word ? `${patients.length} of ${all.length} patients` : `${all.length} patient${all.length === 1 ? "" : "s"}`}>
              <Table headers={["Patient", "Status", "Identity", "Own login", "Clinicians with a care relationship", "Guardians with a proxy", "Details"]}
                empty={word ? "No patient matches that name." : "No patients yet."}
                rows={patients.map((p) => [
                  <Patient key="i" name={p.patient_name} id={p.id} />,
                  <Pill key="s" kind={p.status === "active" ? "ok" : "bad"}>{p.status}</Pill>,
                  <Pill key="v" kind={p.identity_status === "verified" ? "ok" : "warn"}>{p.identity_status}</Pill>,
                  p.has_login ? "yes" : <Pill key="n" kind="warn">no login</Pill>,
                  p.care.filter((c) => c.status === "active" && !c.contested).map((c) => c.clinician).join(", ") || "none",
                  p.proxies.filter((g) => g.status === "active").map((g) => `${g.guardian} (${g.type})`).join(", ") || "none",
                  <button key="d" className="btn ghost" onClick={() => setEditing(p.id)}>
                    {p.patient_name ? "Edit" : "Add details"}
                  </button>,
                ])} />
            </Card>
          </div>
          {editing && (
            <div className="grid">
              <PatientDetails person={editing} onClose={() => setEditing("")}
                onSaved={() => { setEditing(""); setVersion((v) => v + 1); }} />
              <PatientCoverage person={editing} />
            </div>
          )}
        </>
        );
      }}
    </Gate>
  );
}

export function Organizations() {
  const result = useLoad(getDirectory);
  return (
    <Gate result={result}>
      {(dir) => {
        const staffAt = (orgId: string) =>
          dir.staff.flatMap((m) =>
            m.roles.filter((r) => r.organization_id === orgId && r.status === "active").map((r) => `${m.login} (${r.role})`));
        return (
          <>
            <div className="hero"><div>
              <h1>Organizations</h1>
              <p>Clinics, labs and companies, and the staff holding roles at each. Patient accounts are not listed here.</p>
            </div></div>
            <div className="grid">
              <Card title={`${dir.organizations.length} organization${dir.organizations.length === 1 ? "" : "s"}`}>
                <Table headers={["Organization", "Status", "Staff and roles"]} empty="No organizations yet."
                  rows={dir.organizations.map((o) => [
                    o.name,
                    <Pill key="s" kind={o.status === "active" ? "ok" : "bad"}>{o.status}</Pill>,
                    staffAt(o.id).join(", ") || "nobody yet",
                  ])} />
              </Card>
              <Card title={`${dir.staff.length} staff login${dir.staff.length === 1 ? "" : "s"}`}>
                <Table headers={["Login", "Type", "Status", "Roles"]} empty="No staff yet."
                  rows={dir.staff.map((m) => [
                    m.login, m.type, <Pill key="s" kind={m.status === "active" ? "ok" : "bad"}>{m.status}</Pill>,
                    m.roles.filter((r) => r.status === "active").map((r) => `${r.role} at ${r.organization_name}`).join(", ") || "none",
                  ])} />
              </Card>
            </div>
          </>
        );
      }}
    </Gate>
  );
}
