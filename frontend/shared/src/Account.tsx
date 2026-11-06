import { useEffect, useState } from "react";
import { ChangePassword } from "./ChangePassword";
import { EmailCard, type EmailStatus } from "./EmailCard";
import { NameCard } from "./NameCard";
import { Card, Pill } from "./ui";

export interface NameCheckInfo {
  state: "none" | "verified" | "name_changed" | "withdrawn";
  basis_text?: string; checked_on?: string; reason?: string;
}

/** Whether the clinic has checked the name this person is shown by (patients see a Verified mark). */
function NameCheckCard({ load }: { load: () => Promise<NameCheckInfo> }) {
  const [info, setInfo] = useState<NameCheckInfo | null>(null);
  useEffect(() => { void load().then(setInfo).catch(() => setInfo(null)); }, [load]);
  if (!info) return null;
  const words: Record<NameCheckInfo["state"], string> = {
    none: "Your name has not been checked yet. An administrator at your clinic can check it.",
    verified: `Your name was checked: ${info.basis_text ?? ""} on ${info.checked_on ?? ""}. Patients see a Verified mark next to it.`,
    name_changed: "You changed your name after it was checked, so the Verified mark is off until an administrator checks the new name.",
    withdrawn: `The check of your name was withdrawn${info.reason ? `: ${info.reason}` : ""}. Patients do not see a Verified mark.`,
  };
  return (
    <Card title="Is my name checked?">
      <p><Pill kind={info.state === "verified" ? "ok" : info.state === "none" ? "info" : "warn"}>
        {info.state === "verified" ? "Verified" : info.state === "none" ? "Not checked" : info.state === "name_changed" ? "Check ended" : "Check withdrawn"}
      </Pill></p>
      <p>{words[info.state]}</p>
    </Card>
  );
}

/** "My account" for the apps that have no profile page of their own: who is signed in, and the
 *  change-password form. Nothing here is read from the server: it is what the sign-in returned. */
export function Account({ login, tenantName, roles, onChangePassword, name, email, nameCheck }: {
  login: string; tenantName: string; roles: string[];
  /** Optional: the state of the clinic's check of this person's name (doctors only). */
  nameCheck?: () => Promise<NameCheckInfo>;
  onChangePassword: (current: string, next: string) => Promise<void>;
  name: {
    load: () => Promise<string>;
    onSave: (name: string) => Promise<string>;
    onClear: () => Promise<void>;
  };
  email: {
    load: () => Promise<EmailStatus>;
    onSave: (email: string, password: string) => Promise<EmailStatus & { sent: boolean }>;
    onResend: () => Promise<"sent" | "wait">;
  };
}) {
  return (
    <>
      <div className="hero"><div>
        <h1>My account</h1>
        <p>Who you are signed in as, and your password.</p>
      </div></div>
      <div className="grid">
        <Card title="Signed in as">
          <p><b>{login}</b></p>
          <p>Organization: {tenantName}</p>
          <p>Roles: {roles.length ? roles.join(", ") : "none"}</p>
        </Card>
        <NameCard {...name} />
        {nameCheck && <NameCheckCard load={nameCheck} />}
        <EmailCard {...email} />
        <ChangePassword onChange={onChangePassword} />
      </div>
    </>
  );
}
