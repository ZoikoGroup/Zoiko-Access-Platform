import { useCallback, useEffect, useState } from "react";
import { ApiError, resendVerification, getBookings, getMe, getReferrals, getNotifications, loadSession, setSession, signOutEverywhere, type Booking, type Me, type Notice, type Referral, type Session } from "./api";
import { displayName } from "./format";
import { Appointments } from "./pages/Appointments";
import { Dashboard } from "./pages/Dashboard";
import { FindCare } from "./pages/FindCare";
import { clearLink, LinkPage, readLinkAction } from "./pages/LinkActions";
import { Notifications } from "./pages/Notifications";
import { Planned } from "./pages/Planned";
import { Referrals } from "./pages/Referrals";
import { SignIn } from "./pages/SignIn";
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
  { id: "careteam", label: "My Care Team", icon: "providers", waitsFor: "The backend already records which clinicians treat you (care relationships); a screen for it is not built, and clinician names need provider profiles." },
  { id: "family", label: "Family Members", icon: "patients", waitsFor: "The backend rules for guardians and proxies exist; the screen to manage them is not built." },
  { id: "bills", label: "Bills & Payments", icon: "billing", waitsFor: "Bills and payments are a later commercial phase." },
  { id: "insurance", label: "Insurance & Claims", icon: "compliance", waitsFor: "Coverage and claims are a later commercial phase." },
  { id: "messages", label: "Messages", icon: "messages", waitsFor: "Messages arrive with comms (backend step B9)." },
  { id: "notifications", label: "Notifications", icon: "bell" },
  { id: "privacy", label: "Privacy & Consent", icon: "shield", waitsFor: "Consent and purpose grants are a later policy step; today access is decided by roles and relationships only." },
  { id: "profile", label: "My Profile", icon: "settings", waitsFor: "Profiles need encrypted patient details (DEV-024), not built yet." },
  { id: "help", label: "Help & Support", icon: "integrations", waitsFor: "Support tooling is not built yet." },
];
const LIVE: PageId[] = ["dashboard", "find", "appointments", "referrals", "notifications"];

function startPage(): PageId {
  const wanted = new URLSearchParams(window.location.search).get("page");
  return NAV.find((n) => n.id === wanted)?.id ?? "dashboard";
}

export default function App() {
  const [session, setSessionState] = useState<Session | null>(loadSession());
  const [me, setMe] = useState<Me | null>(null);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [unread, setUnread] = useState(0);
  const [page, setPage] = useState<PageId>(startPage());
  const [error, setError] = useState("");
  const [link, setLink] = useState(readLinkAction());

  const forget = useCallback(() => {
    setSession(null);
    setSessionState(null);
    setMe(null);
    setBookings([]);
    setReferrals([]);
    setNotices([]);
    setUnread(0);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [who, list, inbox, sent] = await Promise.all([getMe(), getBookings(), getNotifications(), getReferrals()]);
      setMe(who);
      setBookings(list);
      setReferrals(sent);
      setNotices(inbox.notifications);
      setUnread(inbox.unread);
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
  const name = displayName(session.login);
  let body;
  if (!me) body = <div className="notice">Loading…</div>;
  else if (!LIVE.includes(page)) body = <Planned title={item.label} waitsFor={item.waitsFor ?? ""} />;
  else if (page === "find") body = <FindCare me={me} onBooked={refresh} goTo={setPage} />;
  else if (page === "appointments") body = <Appointments bookings={bookings} onChanged={refresh} />;
  else if (page === "referrals") body = <Referrals referrals={referrals} />;
  else if (page === "notifications") body = <Notifications notices={notices} unread={unread} onChanged={refresh} />;
  else body = <Dashboard name={name} me={me} bookings={bookings} notices={notices} goTo={setPage} />;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <Logo />
          <div><b>Zoiko Access</b><small>Global Healthcare Access</small></div>
        </div>
        <label className="search" title="Search is not built yet">
          <Icon name="search" size={18} />
          <input placeholder="Search is not built yet" disabled aria-label="Search (not built yet)" />
        </label>
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
            onClick={() => setPage(n.id)} aria-current={page === n.id ? "page" : undefined}>
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
      <main className="content">
        {me?.email?.email && !me.email.verified && <VerifyBanner address={me.email.email} />}
        {error && <div className="error">Cannot reach the backend: {error}. Is it running (<code>docker compose up -d</code>)?</div>}
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
