import { useCallback, useEffect, useState } from "react";
import { dropDevLogin } from "@shared/devLogin";
import { ApiError, resendVerification, getBookings, getCoverage, getMe, getProfiles, getReferrals, getWaitlist, getNotifications, loadSession, setSession, signOutEverywhere, type Booking, type Me, type Notice, type CoverageRow, type ProfileRow, type Referral, type Session, type WaitEntry } from "./api";
import { displayName } from "./format";
import { Appointments } from "./pages/Appointments";
import { CareTeam } from "./pages/CareTeam";
import { Dashboard } from "./pages/Dashboard";
import { Family } from "./pages/Family";
import { FindCare } from "./pages/FindCare";
import { Insurance } from "./pages/Insurance";
import { Messages } from "./pages/Messages";
import { clearLink, LinkPage, readLinkAction } from "./pages/LinkActions";
import { Notifications } from "./pages/Notifications";
import { Planned } from "./pages/Planned";
import { Bills } from "./pages/Bills";
import { Privacy } from "./pages/Privacy";
import { Profile } from "./pages/Profile";
import { Help } from "./pages/Help";
import { Referrals } from "./pages/Referrals";
import { SignIn } from "./pages/SignIn";
import { QuickSearch } from "./QuickSearch";
import { Icon, Logo } from "./ui";

export type PageId =
  | "dashboard" | "find" | "appointments" | "referrals" | "records" | "careteam" | "family"
  | "bills" | "insurance" | "messages" | "notifications" | "privacy" | "profile" | "help";

interface NavItem { id: PageId; label: string; icon: string; waitsFor?: string }

const NAV: NavItem[] = [
  { id: "dashboard", label: "Home", icon: "dashboard" },
  { id: "find", label: "Find Healthcare", icon: "search" },
  { id: "appointments", label: "Appointments", icon: "appointments" },
  { id: "referrals", label: "My Referrals", icon: "referrals" },
  { id: "records", label: "My Health Records", icon: "services", waitsFor: "Health records need the clinical record service, a later phase." },
  { id: "careteam", label: "My Care Team", icon: "providers" },
  { id: "family", label: "Family Members", icon: "patients" },
  { id: "bills", label: "Bills & Payments", icon: "billing" },
  { id: "insurance", label: "Insurance & Claims", icon: "compliance" },
  { id: "messages", label: "Messages", icon: "messages" },
  { id: "notifications", label: "Notifications", icon: "bell" },
  { id: "privacy", label: "Privacy & Consent", icon: "shield" },
  { id: "profile", label: "My Profile", icon: "settings" },
  { id: "help", label: "Help & Support", icon: "integrations" },
];
const LIVE: PageId[] = ["dashboard", "find", "appointments", "referrals", "notifications", "profile", "insurance", "careteam", "privacy", "family", "messages", "help", "bills"];

function startPage(): PageId {
  const wanted = new URLSearchParams(window.location.search).get("page");
  return NAV.find((n) => n.id === wanted)?.id ?? "dashboard";
}

export default function App() {
  const [session, setSessionState] = useState<Session | null>(loadSession());
  useEffect(() => { if (session) dropDevLogin(); }, [session]);   // development only
  const [me, setMe] = useState<Me | null>(null);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [waiting, setWaiting] = useState<WaitEntry[]>([]);
  const [profiles, setProfiles] = useState<ProfileRow[]>([]);
  const [coverage, setCoverage] = useState<CoverageRow[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [unread, setUnread] = useState(0);
  const [page, setPage] = useState<PageId>(startPage());
  const [findQuery, setFindQuery] = useState("");        // set by the top-bar search
  const openPage = (id: PageId) => { setFindQuery(""); setPage(id); };
  const [error, setError] = useState("");
  const [asOf, setAsOf] = useState<number | null>(null);   // when the data on screen last loaded
  const [link, setLink] = useState(readLinkAction());

  const forget = useCallback(() => {
    setSession(null);
    setSessionState(null);
    setMe(null);
    setBookings([]);
    setReferrals([]);
    setWaiting([]);
    setProfiles([]);
    setCoverage([]);
    setNotices([]);
    setUnread(0);
    setAsOf(null);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [who, list, inbox, sent, queue, mine, cover] = await Promise.all([
        getMe(), getBookings(), getNotifications(), getReferrals(), getWaitlist(), getProfiles(),
        getCoverage()]);
      setMe(who);
      setBookings(list);
      setReferrals(sent);
      setWaiting(queue);
      setProfiles(mine);
      setCoverage(cover);
      setNotices(inbox.notifications);
      setUnread(inbox.unread);
      setAsOf(Date.now());
      setError("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) forget();
      else setError(e instanceof Error ? e.message : "Backend unreachable");
    }
  }, [forget]);

  useEffect(() => {
    if (!session) return;
    void refresh();
    // Not too often: every check is recorded in the audit log.
    const timer = setInterval(() => void refresh(), 60_000);
    return () => clearInterval(timer);
  }, [session, refresh]);

  if (link) {
    return <LinkPage link={link} onDone={() => { clearLink(); setLink(null); }} />;
  }

  if (!session) {
    return <SignIn onSignedIn={(s) => { setSession(s); setSessionState(s); }} />;
  }

  const item = NAV.find((n) => n.id === page) ?? NAV[0];
  // The patient's own saved name (My Profile) when there is one; otherwise a name made from the login.
  const ownName = profiles.find((x) => x.own)?.name.trim() ?? "";
  const name = ownName ? ownName.split(" ")[0] : displayName(session.login);
  let body;
  if (!me) body = <div className="notice">Loading…</div>;
  else if (!LIVE.includes(page)) body = <Planned title={item.label} waitsFor={item.waitsFor ?? ""} />;
  else if (page === "find") body = <FindCare key={findQuery} initialQuery={findQuery} me={me} waiting={waiting} profiles={profiles} onBooked={refresh} goTo={setPage} />;
  else if (page === "appointments") body = <Appointments bookings={bookings} waiting={waiting} onChanged={refresh} />;
  else if (page === "messages") body = <Messages onChanged={refresh} />;
  else if (page === "help") body = <Help bookings={bookings} goTo={setPage} />;
  else if (page === "family") body = <Family onChanged={refresh} />;
  else if (page === "careteam") body = <CareTeam />;
  else if (page === "privacy") body = <Privacy />;
  else if (page === "bills") body = <Bills />;
  else if (page === "insurance") body = <Insurance coverage={coverage} hasPatient={me.persons.length > 0} onChanged={refresh} />;
  else if (page === "profile") body = <Profile key={profiles.map((x) => x.name).join("|")} profiles={profiles} onSaved={refresh} />;
  else if (page === "referrals") body = <Referrals referrals={referrals} />;
  else if (page === "notifications") body = <Notifications notices={notices} unread={unread} onChanged={refresh} goToProfile={() => setPage("profile")} />;
  else body = <Dashboard name={name} me={me} bookings={bookings} notices={notices} referrals={referrals} goTo={setPage} />;

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to main content</a>
      <header className="topbar">
        <div className="brand">
          <Logo />
          <div><b>Zoiko Access</b><small>Global Healthcare Access</small></div>
        </div>
        <QuickSearch pages={NAV.filter((n) => LIVE.includes(n.id))} onPage={(id) => openPage(id as PageId)}
          fallback={{ icon: "search", label: (t) => `Find care “${t}”`, onChoose: (t) => { setFindQuery(t); setPage("find"); } }} />
        <span className="spacer" />
        <button className="bell" onClick={() => setPage("notifications")}
          aria-label={`Notifications, ${unread} unread`} title="Notifications">
          <Icon name="bell" size={20} />
          {unread > 0 && <span className="bell-badge">{unread > 9 ? "9+" : unread}</span>}
        </button>
        <span className="chip" title="Your care provider group">{me?.tenant ?? "…"}</span>
        <div className="user">
          <div className="avatar">{name.slice(0, 2).toUpperCase()}</div>
          <div><b>{name}</b><small>Patient</small></div>
        </div>
        <button className="btn ghost" onClick={() => { void signOutEverywhere().then(forget); }}>Sign out</button>
      </header>
      <nav className="sidebar" aria-label="Main">
        {NAV.map((n) => (
          <button key={n.id} className={`nav-item ${page === n.id ? "active" : ""}`}
            onClick={() => openPage(n.id)} aria-current={page === n.id ? "page" : undefined}>
            <Icon name={n.icon} />
            {n.label}
            {!LIVE.includes(n.id) && <span className="soon">soon</span>}
            {n.id === "notifications" && unread > 0 && <span className="count">{unread}</span>}
          </button>
        ))}
        <div className="side-foot">
          <div>Signed in as {session.login}</div>
          <div className="cell">{me?.cell ?? "…"}</div>
        </div>
      </nav>
      <main className="content" id="main" tabIndex={0}>
        {me?.email?.email && !me.email.verified && <VerifyBanner address={me.email.email} />}
        {error && (
          <div className="error" role="alert">
            Cannot reach the backend: {error}. Is it running (<code>docker compose up -d</code>)?
            {asOf && ` The information below is old: it was last updated at ${new Date(asOf).toLocaleTimeString()}.`}
          </div>
        )}
        {body}
      </main>
    </div>
  );
}

function VerifyBanner({ address }: { address: string }) {
  const [state, setState] = useState<"idle" | "sent" | "wait" | "error">("idle");
  async function resend() {
    try { setState(await resendVerification()); } catch { setState("error"); }
  }
  return (
    <div className="notice" role="status" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
      <span>Please verify your email address ({address}). We sent you a link when you signed up.</span>
      <button className="btn ghost" onClick={() => void resend()} disabled={state === "sent"}>
        {state === "sent" ? "Email sent" : "Resend email"}
      </button>
      {state === "wait" && <span>Please wait a minute before asking again.</span>}
      {state === "error" && <span>Could not send right now.</span>}
    </div>
  );
}
