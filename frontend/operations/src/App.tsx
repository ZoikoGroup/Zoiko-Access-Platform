import { useCallback, useEffect, useState } from "react";
import { checkHealth, getMe, loadSession, setSession, signOutEverywhere, staffFromSession, type Session } from "./api";
import { NAV, type PageId } from "./nav";
import { Sidebar, Topbar } from "./Shell";
import type { Health } from "./types";
import { useLoad } from "./useLoad";
import { Compliance } from "./pages/Compliance";
import { Appointments } from "./pages/Appointments";
import { Dashboard } from "./pages/Dashboard";
import { Organizations, Patients } from "./pages/Lists";
import { Planned } from "./pages/Planned";
import { Referrals } from "./pages/Referrals";
import { Services } from "./pages/Services";
import { Team } from "./pages/Team";
import { SignIn } from "./pages/SignIn";
import { clearLink, LinkPage, readLinkAction } from "./pages/LinkActions";

function startPage(): PageId {
  const wanted = new URLSearchParams(window.location.search).get("page");
  return NAV.find((n) => n.id === wanted)?.id ?? "dashboard";
}

export default function App() {
  const [session, setSessionState] = useState<Session | null>(loadSession());
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
  else if (page === "patients") body = <Patients />;
  else if (page === "organizations") body = <Organizations />;
  else if (page === "team") body = <Team />;
  else if (page === "referrals") body = <Referrals />;
  else if (page === "compliance") body = <Compliance />;
  else body = <Dashboard tenantName={session.tenantName} api={health.api} database={health.database} />;

  return (
    <div className="shell">
      <Topbar tenantName={session.tenantName} login={session.login}
        roles={session.roles.map((r) => r.role)} onSignOut={onSignOut} />
      <Sidebar page={page} onPage={setPage} cell={me.data?.cell ?? ""} api={health.api} database={health.database} />
      <main className="content">{body}</main>
    </div>
  );
}
