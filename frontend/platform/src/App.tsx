import { useCallback, useEffect, useState } from "react";
import { ApiError, checkHealth, getOverview, loadSession, setSession, signOutEverywhere, type Session } from "./api";
import { NAV, type PageId } from "./nav";
import { Sidebar, Topbar } from "./Shell";
import type { Health, Overview } from "./types";
import { Card } from "./ui";
import { CustomersTable } from "./pages/Customers";
import { OverviewPage } from "./pages/Overview";
import { Planned } from "./pages/Planned";
import { SignIn } from "./pages/SignIn";

export default function App() {
  const [session, setSessionState] = useState<Session | null>(loadSession());
  if (!session) {
    return <SignIn onSignedIn={(s) => { setSession(s); setSessionState(s); }} />;
  }
  const signOut = () => { void signOutEverywhere().then(() => setSessionState(null)); };
  return <Console session={session} onSignOut={signOut} />;
}

function Console({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const [page, setPage] = useState<PageId>("overview");
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(true);
  const [health, setHealth] = useState<{ api: Health; database: Health }>({ api: "checking", database: "checking" });

  // Every load is written to the platform audit log, so we load on open and on request: no polling.
  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      setData(await getOverview());
      setError(""); setDenied(false);
    } catch (e) {
      setDenied(e instanceof ApiError && e.code === "POLICY_DENIED");
      setError(e instanceof Error ? e.message : "Could not load");
    }
    setHealth(await checkHealth());
    setBusy(false);
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const item = NAV.find((n) => n.id === page) ?? NAV[0];
  let body;
  if (!item.live) body = <Planned item={item} />;
  else if (denied) body = <div className="notice">Your account is not allowed to use the platform console.</div>;
  else if (!data) body = error ? <div className="error">{error}</div> : <div className="notice">Loading…</div>;
  else if (page === "customers") {
    body = (
      <>
        <div className="hero">
          <div><h1>Customers</h1><p>Every customer in cell {data.cell}, in detail. Numbers only.</p></div>
          <span className="spacer" />
          <button className="btn" onClick={() => void refresh()} disabled={busy}>Refresh</button>
        </div>
        <div className="grid"><Card title={`${data.tenants.length} customers`}><CustomersTable rows={data.tenants} detailed /></Card></div>
      </>
    );
  } else {
    body = <OverviewPage data={data} api={health.api} database={health.database} onRefresh={() => void refresh()} busy={busy} />;
  }

  return (
    <div className="shell">
      <Topbar tenantName={session.tenantName} login={session.login}
        roles={session.roles.map((r) => r.role)} onSignOut={onSignOut} />
      <Sidebar page={page} onPage={setPage} cell={data?.cell ?? ""} api={health.api} database={health.database} />
      <main className="content">{error && data && <div className="error">{error}</div>}{body}</main>
    </div>
  );
}
