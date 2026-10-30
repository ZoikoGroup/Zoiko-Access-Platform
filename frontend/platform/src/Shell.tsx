import { NAV, type PageId } from "./nav";
import type { Health } from "./types";
import { Icon, Logo } from "./ui";

export function Topbar({ tenantName, login, roles, onSignOut }: {
  tenantName: string; login: string; roles: string[]; onSignOut: () => void;
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
      <label className="search" title="Search arrives with the first data screens">
        <Icon name="search" size={18} />
        <input placeholder="Search is not built yet" disabled aria-label="Search (not built yet)" />
      </label>
      <span className="spacer" />
      <span className="chip" title="Your organization group"><Icon name="organizations" size={16} />{tenantName}</span>
      <div className="user">
        <div className="avatar">{login.slice(0, 2).toUpperCase()}</div>
        <div>
          <b>{login}</b>
          <small>{roles.join(", ") || "no role"}</small>
        </div>
      </div>
      <button className="btn ghost" onClick={onSignOut}>Sign out</button>
    </header>
  );
}

const HEALTH_TEXT: Record<Health, string> = { ok: "Operational", down: "Unreachable", checking: "Checking" };

export function Sidebar({ page, onPage, cell, api, database }: {
  page: PageId; onPage: (id: PageId) => void; cell: string; api: Health; database: Health;
}) {
  return (
    <nav className="sidebar" aria-label="Main">
      {NAV.map((item) => (
        <button key={item.id} className={`nav-item ${page === item.id ? "active" : ""}`}
          onClick={() => onPage(item.id)} aria-current={page === item.id ? "page" : undefined}>
          <Icon name={item.icon} />
          {item.label}
          {!item.live && <span className="soon">soon</span>}
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
