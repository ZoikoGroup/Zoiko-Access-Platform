import { Pill } from "@shared/ui";

export * from "@shared/ui";

/** A small "Verified" mark next to a doctor's name, only when the clinic has checked that name. */
export function VerifiedMark({ on }: { on?: boolean }) {
  return on ? <Pill kind="ok">Verified</Pill> : null;
}
