import { NAV, type PageId } from "./nav";
import type { Health } from "./types";
import { QuickSearch } from "./QuickSearch";
import { Icon, Logo } from "./ui";

export function Topbar({ displayName = "", tenantName, login, roles, onSignOut, onGo }: {
  displayName?: string; tenantName: string; login: string; roles: string[]; onSignOut: () => void;
  onGo: (page: PageId, patientQuery?: string) => void;
}) {
  return (
    <header className="topbar">
      <div className="brand">
        <Logo />
        <div>
          <b>Zoiko Access</b>
          <small>Global Healthcare Access</small>
        </div>
      </div>
      <QuickSearch pages={NAV.filter((n) => n.live)} onPage={(id) => onGo(id as PageId)}
        fallback={{ icon: "patients", label: (t) => `Find my patient “${t}”`, onChoose: (t) => onGo("patients", t) }} />
      <span className="spacer" />
      <span className="chip" title="Your organization group"><Icon name="organizations" size={16} />{tenantName}</span>
      <div className="user">
        <div className="avatar">{(displayName || login).slice(0, 2).toUpperCase()}</div>
        <div>
          <b title={displayName ? login : undefined}>{displayName || login}</b>
          <small>{roles.join(", ") || "no role"}</small>
        </div>
      </div>
      <button className="btn ghost" onClick={onSignOut}>Sign out</button>
    </header>
  );
}

const HEALTH_TEXT: Record<Health, string> = { ok: "Operational", down: "Unreachable", checking: "Checking" };

export function Sidebar({ page, onPage, cell, api, database, badges = {} }: {
  page: PageId; onPage: (id: PageId) => void; cell: string; api: Health; database: Health;
  badges?: Partial<Record<PageId, number>>;     // how many things wait on a page, shown beside its name
}) {
  return (
    <nav className="sidebar" aria-label="Main">
      {NAV.map((item) => (
        <button key={item.id} className={`nav-item ${page === item.id ? "active" : ""}`}
          onClick={() => onPage(item.id)} aria-current={page === item.id ? "page" : undefined}>
          <Icon name={item.icon} />
          {item.label}
          {!item.live && <span className="soon">soon</span>}
          {(badges[item.id] ?? 0) > 0 && (
            <span className="soon" aria-label={`${badges[item.id]} waiting`}>{badges[item.id]}</span>
          )}
        </button>
      ))}
      <div className="side-foot">
        <div>Region / Data cell</div>
        <div className="cell">{cell || "…"}</div>
        <div><i className={`dot ${api}`} />API: {HEALTH_TEXT[api]}</div>
        <div style={{ marginTop: 6 }}><i className={`dot ${database}`} />Database: {HEALTH_TEXT[database]}</div>
      </div>
    </nav>
  );
}
