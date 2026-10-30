import { getDirectory, getPatients } from "../api";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table, shortId } from "../ui";

export function Patients() {
  const result = useLoad(getPatients);
  return (
    <Gate result={result}>
      {(patients) => (
        <>
          <div className="hero"><div>
            <h1>Patients</h1>
            <p>Patient records and who can reach each one. Names, birth dates and emails are not shown: patients appear as ids (DEV-024).</p>
          </div></div>
          <div className="grid">
            <Card title={`${patients.length} patient${patients.length === 1 ? "" : "s"}`}>
              <Table headers={["Patient", "Status", "Identity", "Own login", "Clinicians with a care relationship", "Guardians with a proxy"]}
                empty="No patients yet."
                rows={patients.map((p) => [
                  <span key="i" className="mono">{shortId(p.id)}</span>,
                  <Pill key="s" kind={p.status === "active" ? "ok" : "bad"}>{p.status}</Pill>,
                  <Pill key="v" kind={p.identity_status === "verified" ? "ok" : "warn"}>{p.identity_status}</Pill>,
                  p.has_login ? "yes" : <Pill key="n" kind="warn">no login</Pill>,
                  p.care.filter((c) => c.status === "active" && !c.contested).map((c) => c.clinician).join(", ") || "none",
                  p.proxies.filter((g) => g.status === "active").map((g) => `${g.guardian} (${g.type})`).join(", ") || "none",
                ])} />
            </Card>
          </div>
        </>
      )}
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
