import { useCallback, useEffect, useState } from "react";
import { checkHealth, getMe, loadSession, setSession, signOutEverywhere, type Session } from "./api";
import { NAV, type PageId } from "./nav";
import { Patients } from "./pages/Patients";
import { Planned } from "./pages/Planned";
import { Referrals } from "./pages/Referrals";
import { clearLink, LinkPage, readLinkAction } from "./pages/LinkActions";
import { Schedule } from "./pages/Schedule";
import { SignIn } from "./pages/SignIn";
import { Sidebar, Topbar } from "./Shell";
import type { Health } from "./types";
import { useLoad } from "./useLoad";

function startPage(): PageId {
  const wanted = new URLSearchParams(window.location.search).get("page");
  return NAV.find((n) => n.id === wanted)?.id ?? "schedule";
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
  const me = useLoad(getMe, 120_000);

  const poll = useCallback(async () => setHealth(await checkHealth()), []);
  useEffect(() => {
    void poll();
    const timer = setInterval(() => void poll(), 15_000);
    return () => clearInterval(timer);
  }, [poll]);

  const item = NAV.find((n) => n.id === page) ?? NAV[0];
  let body;
  if (!item.live) body = <Planned item={item} />;
  else if (page === "patients") body = <Patients />;
  else if (page === "referrals") body = <Referrals />;
  else body = <Schedule />;

  return (
    <div className="shell">
      <Topbar tenantName={session.tenantName} login={session.login}
        roles={session.roles.map((r) => r.role)} onSignOut={onSignOut} />
      <Sidebar page={page} onPage={setPage} cell={me.data?.cell ?? ""} api={health.api} database={health.database} />
      <main className="content">{body}</main>
    </div>
  );
}
