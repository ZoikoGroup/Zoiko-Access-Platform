// Shapes returned by the staff console's partner connection, journey and integration endpoints
// (backend/api/console.py). References, states and codes only: no patient names or details.

export type OperationState =
  | "pending" | "sent" | "accepted" | "confirmed" | "rejected" | "unknown" | "dead";

export interface ConnectorOp {
  public_id: string;
  connector_key: string;
  operation_type: string;
  subject_ref: string;
  organization_ref: string;
  state: OperationState;
  attempts: number;
  polls: number;
  partner_ref: string;
  last_code: string;
  last_error: string;
  created_at: string;
  sent_at: string | null;
  finished_at: string | null;
  /** True only when the partner itself confirmed. A person settling it by hand is never this. */
  source_confirmed: boolean;
}

export interface JourneyException {
  public_id: string;
  instance_ref: string;
  kind: string;
  detail: string;
  status: "open" | "resolved";
  raised_at: string;
  resolved_at: string | null;
  subject_ref: string;
  journey: string;
  journey_state: string;
}

export interface ExceptionsData {
  can_act: boolean;
  connector: { attention: ConnectorOp[]; waiting: ConnectorOp[]; recent: ConnectorOp[] };
  journeys: JourneyException[];
}

export interface OperationHistory {
  operation_ref: string;
  state: OperationState;
  chain_ok: boolean;
  bad_seq: number | null;
  events: { seq: number; event_type: string; state_after: string; code: string; occurred_at: string; by: string }[];
}

export interface JourneyDefinition {
  public_id: string;
  key: string;
  version: number;
  state: "released" | "retired";
  body_digest: string;
  released_at: string;
  initial: string;
  states: string[];
  terminal: Record<string, string>;
  transitions: string[];
  timers: Record<string, number>;
}

export interface JourneyInstance {
  public_id: string;
  definition_key: string;
  definition_version: number;
  subject_ref: string;
  state: string;
  status: "running" | "completed" | "cancelled";
  owner_ref: string;
  zone: string;
  created_at: string;
  completed_at: string | null;
}

export interface JourneysData {
  definitions: JourneyDefinition[];
  instances: JourneyInstance[];
  by_state: Record<string, number>;
}

export interface JourneyHistory {
  instance_ref: string;
  chain_ok: boolean;
  bad_seq: number | null;
  steps: {
    seq: number; transition: string; from_state: string; to_state: string; by: string;
    actor_kind: string; evidence: { kind: string; ref: string }[]; occurred_at: string;
  }[];
}

export interface DeveloperDelivery {
  public_id: string;
  event_id: string;
  event_type: string;
  state: "pending" | "delivered" | "dead";
  attempts: number;
  last_status: number | null;
  last_error: string;
  delivered_at: string | null;
}

export interface DeveloperWebhook {
  public_id: string;
  url: string;
  event_prefixes: string[];
  status: "active" | "paused" | "disabled";
  dead_streak: number;
  deliveries: DeveloperDelivery[];
}

export interface DeveloperClient {
  public_id: string;
  name: string;
  environment: "sandbox" | "production";
  scopes: string[];
  status: "active" | "revoked";
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  webhooks: DeveloperWebhook[];
}

export interface DeveloperData {
  can_manage: boolean;
  clients: DeveloperClient[];
  scopes: string[];
  event_families: string[];
}
