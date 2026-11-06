import { changePassword, clearDisplayName, getDisplayName, getEmailStatus, getSettings, resendVerification, saveDisplayName, setEmail, type Staff } from "../api";
import { ChangePassword } from "../ChangePassword";
import { EmailCard } from "../EmailCard";
import { NameCard } from "../NameCard";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table } from "../ui";
import { NoStaff } from "./Appointments";

export function Settings({ staff }: { staff: Staff | null }) {
  const result = useLoad(getSettings, 60_000);
  if (!staff) return <NoStaff />;
  return (
    <Gate result={result}>
      {(s) => (
        <>
          <div className="hero"><div>
            <h1>Settings</h1>
            <p>The rules {staff.organizationName} runs by today. They are read-only here: they are set for the whole service by whoever runs it, and choosing different numbers per clinic is an open decision.</p>
          </div></div>
          <div className="grid">
            <Card title="Timing rules">
              <Table headers={["Rule", "Value", "What it does"]} empty=""
                rows={[
                  ["Response target", `${s.response_target_hours} hours`, "A booking or message waiting longer is flagged late on the Dashboard and Appointments, and in the daily email."],
                  ["Appointment reminder", `${s.reminder_lead_hours} hours before`, "Patients are reminded this long before a confirmed appointment."],
                  ["Waiting-list offer", `${s.waitlist_offer_hours} hours`, "An offered time nobody used goes back to the next person after this."],
                ]} />
            </Card>
            <Card title="Daily late-work email">
              {s.late_email_recipients === 0
                ? <p><Pill kind="warn">nobody would receive it</Pill> No clinic operations staff member has a verified email address, so late work is only visible on screen. An address is verified when its owner clicks the link in the email we send them (for example the invitation).</p>
                : <p>{s.late_email_recipients} clinic operations {s.late_email_recipients === 1 ? "person" : "people"} would receive it, once a day, when something is late.</p>}
            </Card>
            <NameCard load={getDisplayName} onSave={saveDisplayName} onClear={clearDisplayName} />
            <EmailCard load={getEmailStatus} onSave={setEmail} onResend={resendVerification} />
            <ChangePassword onChange={changePassword} />
          </div>
        </>
      )}
    </Gate>
  );
}
