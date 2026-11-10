import { useCallback, useEffect, useState } from "react";
import { dropDevLogin } from "@shared/devLogin";
import { checkHealth, getDisplayName, getMe, loadSession, setSession, signOutEverywhere, staffFromSession, type Session } from "./api";
import { NAV, type PageId } from "./nav";
import { Sidebar, Topbar } from "./Shell";
import type { Health } from "./types";
import { useLoad } from "./useLoad";
import { Compliance } from "./pages/Compliance";
import { Appointments } from "./pages/Appointments";
import { Dashboard } from "./pages/Dashboard";
import { Organizations, Patients } from "./pages/Lists";
import { Planned } from "./pages/Planned";
import { Providers } from "./pages/Providers";
import { Settings } from "./pages/Settings";
import { WorkQueue } from "./pages/WorkQueue";
import { FamilyRequests } from "./pages/FamilyRequests";
import { Messages } from "./pages/Messages";
import { Referrals } from "./pages/Referrals";
import { Reports } from "./pages/Reports";
import { WaitingList } from "./pages/WaitingList";
import { Services } from "./pages/Services";
import { AiPermits } from "./pages/AiPermits";
import { Team } from "./pages/Team";
import { SignIn } from "./pages/SignIn";
import { clearLink, LinkPage, readLinkAction } from "./pages/LinkActions";

function startPage(): PageId {
  const wanted = new URLSearchParams(window.location.search).get("page");
  return NAV.find((n) => n.id === wanted)?.id ?? "dashboard";
}

export default function App() {
  const [session, setSessionState] = useState<Session | null>(loadSession());
  useEffect(() => { if (session) dropDevLogin(); }, [session]);   // development only
  const [link, setLink] = useState(readLinkAction());
  if (link) {
    return <LinkPage link={link} onDone={() => { clearLink(); setLink(null); }} />;
  }
  if (!session) {
    return <SignIn onSignedIn={(s) => { setSession(s); setSessionState(s); }} />;
  }
  const signOut = () => { void signOutEverywhere().then(() => setSessionState(null)); };
  return <Console session={session} onSignOut={signOut} />;
}

function Console({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const [page, setPage] = useState<PageId>(startPage());
  const [shownName, setShownName] = useState("");      // the name this person chose, if any
  useEffect(() => {
    getDisplayName().then(setShownName).catch(() => setShownName(""));
  }, [page, session.token]);                            // re-read after visiting another page
  const [patientQuery, setPatientQuery] = useState("");      // set by the top-bar search
  const [health, setHealth] = useState<{ api: Health; database: Health }>({ api: "checking", database: "checking" });
  const me = useLoad(getMe, 60_000);

  const pollHealth = useCallback(async () => setHealth(await checkHealth()), []);
  useEffect(() => {
    void pollHealth();
    const timer = setInterval(() => void pollHealth(), 10_000);
    return () => clearInterval(timer);
  }, [pollHealth]);

  const staff = staffFromSession(session);
  const item = NAV.find((n) => n.id === page) ?? NAV[0];

  // Every screen below fetches its own data from the signed-in /api/v1 staff endpoints, as you.
  let body;
  if (!item.live) body = <Planned item={item} />;
  else if (page === "services") body = <Services staff={staff} />;
  else if (page === "appointments") body = <Appointments staff={staff} />;
  else if (page === "patients") body = <Patients key={patientQuery} initialSearch={patientQuery} />;
  else if (page === "organizations") body = <Organizations />;
  else if (page === "team") body = <Team />;
  else if (page === "ai") body = <AiPermits />;
  else if (page === "referrals") body = <Referrals />;
  else if (page === "reports") body = <Reports staff={staff} />;
  else if (page === "providers") body = <Providers staff={staff} />;
  else if (page === "settings") body = <Settings staff={staff} />;
  else if (page === "workflows") body = <WorkQueue staff={staff} onGo={setPage} />;
  else if (page === "messages") body = <Messages staff={staff} />;
  else if (page === "family") body = <FamilyRequests staff={staff} />;
  else if (page === "waitlist") body = <WaitingList staff={staff} />;
  else if (page === "compliance") body = <Compliance />;
  else body = <Dashboard tenantName={session.tenantName} api={health.api} database={health.database} onGo={setPage} />;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to main content</a>
      <Topbar displayName={shownName} tenantName={session.tenantName} login={session.login}
        roles={session.roles.map((r) => r.role)} onSignOut={onSignOut}
        onGo={(p, query) => { setPatientQuery(query ?? ""); setPage(p); }} />
      <Sidebar page={page} onPage={(p) => { setPatientQuery(""); setPage(p); }} cell={me.data?.cell ?? ""} api={health.api} database={health.database} />
      <main className="content" id="main" tabIndex={-1}>{body}</main>
    </div>
  );
}
