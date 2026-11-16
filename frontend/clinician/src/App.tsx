import { useCallback, useEffect, useState } from "react";
import { dropDevLogin } from "@shared/devLogin";
import { changePassword, clearDisplayName, getDisplayName, getNameCheck, getPartnerRequests, getThreads, saveDisplayName, checkHealth, getEmailStatus, resendVerification, setEmail, getMe, getPurpose, loadSession, setPurpose, setSession, signOutEverywhere, type Purpose, type Session } from "./api";
import { NAV, type PageId } from "./nav";
import { Account } from "./Account";
import { Patients } from "./pages/Patients";
import { Planned } from "./pages/Planned";
import { Messages } from "./pages/Messages";
import { PartnerResults } from "./pages/PartnerResults";
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
  const me = useLoad(getMe, 120_000);
  const threads = useLoad(getThreads, 60_000);
  const partnerRequests = useLoad(getPartnerRequests, 60_000);
  const badges = {
    messages: (threads.data ?? []).filter((t) => (t.unread_for_me ?? t.unread) > 0).length,
    results: (partnerRequests.data ?? []).filter((r) => r.state === "result_available").length,
  };
  const [why, setWhy] = useState<Purpose>(getPurpose());         // the reason sent with every request

  const poll = useCallback(async () => setHealth(await checkHealth()), []);
  useEffect(() => {
    void poll();
    const timer = setInterval(() => void poll(), 15_000);
    return () => clearInterval(timer);
  }, [poll]);

  const item = NAV.find((n) => n.id === page) ?? NAV[0];
  let body;
  if (!item.live) body = <Planned item={item} />;
  else if (page === "account") body = <Account login={session.login} tenantName={session.tenantName} roles={session.roles.map((r) => r.role)} onChangePassword={changePassword}
    name={{ load: getDisplayName, onSave: saveDisplayName, onClear: clearDisplayName }}
    nameCheck={getNameCheck}
    email={{ load: getEmailStatus, onSave: setEmail, onResend: resendVerification }} />;
  else if (page === "patients") body = <Patients key={patientQuery} initialSearch={patientQuery} />;
  else if (page === "referrals") body = <Referrals />;
  else if (page === "messages") body = <Messages />;
  else if (page === "results") body = <PartnerResults />;
  else body = <Schedule />;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to main content</a>
      <Topbar displayName={shownName} tenantName={session.tenantName} login={session.login}
        roles={session.roles.map((r) => r.role)} onSignOut={onSignOut}
        onGo={(p, query) => { setPatientQuery(query ?? ""); setPage(p); }} />
      <Sidebar page={page} onPage={(p) => { setPatientQuery(""); setPage(p); }} cell={me.data?.cell ?? ""} api={health.api} database={health.database} badges={badges} />
      <main className="content" id="main" tabIndex={0}>
        <div className="notice" role="group" aria-label="Why you are working" style={{ marginBottom: 12 }}>
          <label>Why are you opening patient records?{" "}
            <select value={why} onChange={(e) => { setPurpose(e.target.value as Purpose); setWhy(e.target.value as Purpose); }}>
              <option value="DIRECT_CARE">To treat my patients (direct care)</option>
              <option value="CONTINUITY_OF_CARE">To follow up earlier care (read only)</option>
            </select>
          </label>
          {why === "CONTINUITY_OF_CARE" && <small style={{ display: "block" }}>Read only: you cannot refer, book or decide while following up. Each look is recorded with this reason.</small>}
        </div>
        <div key={why}>{body}</div>
      </main>
    </div>
  );
}
