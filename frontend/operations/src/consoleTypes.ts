// Shapes returned by the staff console's identity matching and proofing endpoints
// (backend/api/identity.py). Masked details and states only: no full names or numbers.

export type MatchState = "possible_match" | "matched_pending_auth" | "linked" | "split_review" | "closed_not_same";
export type MatchFactor = "same" | "close" | "transposed" | "differ" | "missing";

export interface MaskedPerson {
  id: string;
  at_this_clinic: boolean;
  /** All a reviewer sees of a patient: initials, birth year and the last two phone digits. */
  initials: string;
  birth_year: string;
  phone_end: string;
}

export interface MergeInfo {
  id: string;
  state: "merged" | "unmerged";
  kept: string;
  ended: string;
  login_moved: boolean;
  /** Rows that could not move because the surviving record already had the same thing. */
  left_behind: number;
  /** Guardian and doctor access that stays on the ended record and must be approved again. */
  access_not_carried: number;
  /** Records a merge never moves (clinical notes, bills, claims, lab requests, data requests, sharing choices). */
  not_moved: number;
  moved: number;
}

export interface MatchCase {
  public_id: string;
  ref_no: string;
  state: MatchState;
  factors: { name: MatchFactor; birth_date: MatchFactor; phone: MatchFactor };
  flags: string[];
  confidence: "high" | "medium";
  critical: boolean;
  recheck_requested: boolean;
  first_reviewer: string;
  you_agreed_first: boolean;
  created_at: string;
  updated_at: string;
  people: MaskedPerson[];
  history: { seq: number; action: string; state_after: string; note: string; occurred_at: string; by: string }[];
  chain_ok: boolean;
  /** The merge in force for this case, if the two records have been merged. */
  merge: MergeInfo | null;
}

export interface MatchesData { cases: MatchCase[]; can_decide: boolean }

export interface IdentityInfo {
  status: "verified" | "unverified";
  checked_on: string | null;
  valid_until: string | null;
  clinic: string;
  /** Why a patient is not verified right now ("" when they are or were never checked). */
  why_not: "" | "withdrawn" | "check_failed" | "expired" | "details_changed";
  /** Staff only: a possible duplicate of this patient is waiting for a decision. */
  under_review?: boolean;
}

export interface IdentityCheck {
  id: string;
  evidence_type: string;
  outcome: "passed" | "failed";
  clinic: string;
  by: string;
  checked_at: string;
  valid_until: string | null;
  withdrawn: boolean;
  withdraw_reason: string;
}

export interface IdentityData { identity: IdentityInfo; history?: IdentityCheck[] }
