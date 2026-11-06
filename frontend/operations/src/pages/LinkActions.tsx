import { type LinkAction, LinkPage as SharedLinkPage } from "@shared/LinkActions";
import { resetPassword, verifyEmail } from "../api";

export { clearLink, readLinkAction, type LinkAction } from "@shared/LinkActions";

const copy = {
  headline: "Run your clinic's day with confidence.",
  points: ["Manage services and open times", "Confirm and track bookings", "Every action is recorded for audit"],
  verifyEmail,
  resetPassword,
};

/** The page a person lands on after clicking a link in an email. */
export function LinkPage({ link, onDone }: { link: LinkAction; onDone: () => void }) {
  return <SharedLinkPage link={link} onDone={onDone} copy={copy} />;
}
