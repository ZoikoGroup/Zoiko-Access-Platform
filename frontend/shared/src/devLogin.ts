/** DEVELOPMENT ONLY. Vite removes this branch from production builds (import.meta.env.DEV is false there).
 *
 * `?dev_org=<organization>&dev_login=<login>&dev_pw=<password>` makes a sign-in page sign in once, through the
 * real sign-in, so a launcher page can open each app already signed in as a demo account. The password is
 * taken out of the address bar at once. Use it only with the demo accounts from docs/RUNNING-LOCALLY.md,
 * never with a real person's password. */
/** Remove the dev-login parameters from the address without using them (used when someone is already signed in,
 *  so a password never stays in the address bar). */
export function dropDevLogin(): void {
  if (!import.meta.env.DEV) return;
  const q = new URLSearchParams(window.location.search);
  if (q.has("dev_pw") || q.has("dev_login") || q.has("dev_org")) {
    window.history.replaceState({}, "", window.location.pathname);
  }
}

export function readDevLogin(): { org: string; login: string; pw: string } | null {
  if (!import.meta.env.DEV) return null;
  const q = new URLSearchParams(window.location.search);
  const org = q.get("dev_org"), login = q.get("dev_login"), pw = q.get("dev_pw");
  if (!org || !login || !pw) return null;
  window.history.replaceState({}, "", window.location.pathname);
  return { org, login, pw };
}
