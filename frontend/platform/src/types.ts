// Shapes returned by GET /api/v1/platform/overview. Aggregate numbers only: no patient data.

export type Health = "ok" | "down" | "checking";

export interface TenantRow {
  id: string;
  name: string;
  type: string;
  status: string;
  self_registration: boolean;
  created_at: string;
  counts: { patients: number; organizations: number; staff: number; roles_in_force: number; care_links: number };
  services: { active: number; inactive: number };
  bookings: { requested: number; confirmed: number; cancelled: number; open_slots: number };
  audit: {
    chain_ok: boolean; chain_checked: number; chain_bad_seq: number | null;
    last_event_at: string | null; failed_logins: number;
  };
  decisions: { total: number; denied: number };
}

export interface Overview {
  cell: string;
  generated_at: string;
  outbox: { pending: number; quarantined: number; oldest_pending_seconds: number | null };
  mail: { queued: number; failed: number };
  tenants: TenantRow[];
}
