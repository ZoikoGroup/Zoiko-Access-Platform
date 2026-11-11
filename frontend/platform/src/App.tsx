import { useCallback, useEffect, useState } from "react";
import { dropDevLogin } from "@shared/devLogin";
import { ApiError, changePassword, clearDisplayName, getDisplayName, saveDisplayName, checkHealth, getEmailStatus, resendVerification, setEmail, getOverview, loadSession, setSession, signOutEverywhere, type Session } from "./api";
import { Account } from "./Account";
import { NAV, type PageId } from "./nav";
import { Sidebar, Topbar } from "./Shell";
import type { Health, Overview } from "./types";
import { Card } from "./ui";
import { AddCustomer } from "./pages/AddCustomer";
import { CustomersTable } from "./pages/Customers";
import { clearLink, LinkPage, readLinkAction } from "./pages/LinkActions";
import { OverviewPage } from "./pages/Overview";
import { Planned } from "./pages/Planned";
import { Hazards } from "./pages/Hazards";
import { Packs } from "./pages/Packs";
import { Incidents } from "./pages/Incidents";
import { Releases } from "./pages/Releases";
import { Settings } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";

function startPage(): PageId {
  const wanted = new URLSearchParams(window.location.search).get("page");
  return NAV.find((n) => n.id === wanted)?.id ?? "overview";
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
  const [customerQuery, setCustomerQuery] = useState("");   // set by the top-bar search
  const [shownName, setShownName] = useState("");      // the name this person chose, if any
  useEffect(() => {
    getDisplayName().then(setShownName).catch(() => setShownName(""));
  }, [page, session.token]);                            // re-read after visiting another page
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
  const word = customerQuery.trim().toLowerCase();
  const shown = word && data ? data.tenants.filter((t) => t.name.toLowerCase().includes(word)) : data?.tenants ?? [];
  let body;
  if (!item.live) body = <Planned item={item} />;
  else if (page === "account") body = <Account login={session.login} tenantName={session.tenantName} roles={session.roles.map((r) => r.role)} onChangePassword={changePassword}
    name={{ load: getDisplayName, onSave: saveDisplayName, onClear: clearDisplayName }}
    email={{ load: getEmailStatus, onSave: setEmail, onResend: resendVerification }} />;
  else if (page === "hazards") body = <Hazards />;
  else if (page === "packs") body = <Packs />;
  else if (page === "incidents") body = <Incidents canWrite={session.roles.some((r) => r.role === "R11")} />;
  else if (page === "releases") body = <Releases canAdd={session.roles.some((r) => r.role === "R11")} />;
  else if (denied) body = <div className="notice">Your account is not allowed to use the platform console.</div>;
  else if (!data) body = error ? <div className="error">{error}</div> : <div className="notice">Loading…</div>;
  else if (page === "settings") body = <Settings tenants={data.tenants} canWrite={session.roles.some((r) => r.role === "R11")} onChanged={refresh} />;
  else if (page === "customers") {
    body = (
      <>
        <div className="hero">
          <div><h1>Customers</h1><p>Every customer in cell {data.cell}, in detail. Numbers only.</p></div>
          <span className="spacer" />
          <button className="btn" onClick={() => void refresh()} disabled={busy}>Refresh</button>
        </div>
        <div className="grid">
          {session.roles.some((r) => r.role === "R11") && <AddCustomer onAdded={refresh} />}
          <Card title={word ? `${shown.length} of ${data.tenants.length} customers` : `${data.tenants.length} customers`}
            action={<input aria-label="Search customers by name" placeholder="Search by name" value={customerQuery}
              onChange={(e) => setCustomerQuery(e.target.value)}
              style={{ padding: "7px 10px", border: "1px solid var(--line)", borderRadius: 10, background: "var(--bg)" }} />}>
            {word && shown.length === 0 ? <div className="empty">No customer matches that name.</div> : <CustomersTable rows={shown} detailed />}
          </Card>
        </div>
      </>
    );
  } else {
    body = <OverviewPage data={data} api={health.api} database={health.database} onRefresh={() => void refresh()} busy={busy} onGo={setPage} />;
  }

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to main content</a>
      <Topbar displayName={shownName} tenantName={session.tenantName} login={session.login}
        roles={session.roles.map((r) => r.role)} onSignOut={onSignOut}
        onGo={(p, query) => { setCustomerQuery(query ?? ""); setPage(p); }} />
      <Sidebar page={page} onPage={(p) => { setCustomerQuery(""); setPage(p); }} cell={data?.cell ?? ""} api={health.api} database={health.database} />
      <main className="content" id="main" tabIndex={-1}>{error && data && <div className="error">{error}</div>}{body}</main>
    </div>
  );
}
