import { type LinkAction, LinkPage as SharedLinkPage } from "@shared/LinkActions";
import { resetPassword, verifyEmail } from "../api";

export { clearLink, readLinkAction, type LinkAction } from "@shared/LinkActions";

const copy = {
  headline: "Your patients and your day, in one place.",
  points: ["See your appointments as they are confirmed", "See only the patients you care for", "Every look at a patient is recorded"],
  verifyEmail,
  resetPassword,
};

/** The page a person lands on after clicking a link in an email. */
export function LinkPage({ link, onDone }: { link: LinkAction; onDone: () => void }) {
  return <SharedLinkPage link={link} onDone={onDone} copy={copy} />;
}
