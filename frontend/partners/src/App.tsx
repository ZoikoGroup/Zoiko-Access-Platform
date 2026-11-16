import { useCallback, useEffect, useState } from "react";
import { dropDevLogin } from "@shared/devLogin";
import { changePassword, clearDisplayName, getDisplayName, getNameCheck, saveDisplayName, checkHealth, getEmailStatus, resendVerification, setEmail, getMe, loadSession, setSession, signOutEverywhere, type Session } from "./api";
import { navFor, type PageId } from "./nav";
import { Account } from "./Account";
import { Documents } from "./pages/Documents";
import { Home } from "./pages/Home";
import { Keys } from "./pages/Keys";
import { Webhooks } from "./pages/Webhooks";
import { Plans } from "./pages/Plans";
import { Programme } from "./pages/Programme";
import { Requests } from "./pages/Requests";
import { Services } from "./pages/Services";
import { Team } from "./pages/Team";
import { SignIn } from "./pages/SignIn";
import { Sidebar, Topbar } from "./Shell";
import type { Health } from "./types";
import { useLoad } from "./useLoad";

export default function App() {
  const [session, setSessionState] = useState<Session | null>(loadSession());
  useEffect(() => { if (session) dropDevLogin(); }, [session]);   // development only
  if (!session) {
    return <SignIn onSignedIn={(s) => { setSession(s); setSessionState(s); }} />;
  }
  const signOut = () => { void signOutEverywhere().then(() => setSessionState(null)); };
  return <Console session={session} onSignOut={signOut} />;
}

function Console({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const items = navFor(session.area);
  const [page, setPage] = useState<PageId>("home");
  const [shownName, setShownName] = useState("");      // the name this person chose, if any
  useEffect(() => {
    getDisplayName().then(setShownName).catch(() => setShownName(""));
  }, [page, session.token]);
  const [health, setHealth] = useState<{ api: Health; database: Health }>({ api: "checking", database: "checking" });
  const me = useLoad(getMe, 120_000);

  const poll = useCallback(async () => setHealth(await checkHealth()), []);
  useEffect(() => {
    void poll();
    const timer = setInterval(() => void poll(), 15_000);
    return () => clearInterval(timer);
  }, [poll]);

  const item = items.find((n) => n.id === page) ?? items[0];
  let body;
  if (item.id === "account") body = <Account login={session.login} tenantName={session.tenantName} roles={session.roles.map((r) => r.role)} onChangePassword={changePassword}
    name={{ load: getDisplayName, onSave: saveDisplayName, onClear: clearDisplayName }}
    nameCheck={getNameCheck}
    email={{ load: getEmailStatus, onSave: setEmail, onResend: resendVerification }} />;
  else if (item.id === "services") body = <Services />;
  else if (item.id === "team") body = <Team />;
  else if (item.id === "requests") body = <Requests />;
  else if (item.id === "keys") body = <Keys />;
  else if (item.id === "webhooks") body = <Webhooks />;
  else if (item.id === "documents") body = <Documents />;
  else if (item.id === "programme") body = <Programme />;
  else if (item.id === "plans") body = <Plans />;
  else body = <Home />;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to main content</a>
      <Topbar displayName={shownName} tenantName={session.tenantName} login={session.login}
        roles={session.roles.map((r) => r.role)} onSignOut={onSignOut} />
      <Sidebar items={items} page={item.id} onPage={setPage} cell={me.data?.cell ?? ""} api={health.api} database={health.database} />
      <main className="content" id="main" tabIndex={0}>{body}</main>
    </div>
  );
}
