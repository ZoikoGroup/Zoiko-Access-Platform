import { Pill } from "@shared/ui";

export * from "@shared/ui";

/** A small "Name checked" mark next to a doctor's name, only when the clinic says it checked that name.
 *  It is NOT a licence or register verification by Zoiko. */
export function VerifiedMark({ on }: { on?: boolean }) {
  return on ? <Pill kind="ok">Name checked</Pill> : null;
}
