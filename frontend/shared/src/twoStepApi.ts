// Two-step sign-in (DEV-380): the calls every staff app makes, and the code prompt shown before a
// risky action. The rules live on the server (backend/modules/trust/two_step.py); nothing here
// decides anything, and no code, secret or recovery code is ever stored by the browser.

export interface TwoStepStatus {
  available: boolean;          // false for a development link (no password login)
  on: boolean;
  on_since: string | null;
  recovery_codes_left: number;
  setting_up: boolean;
  required: boolean;           // this customer requires it for staff
  recheck_minutes: number;
}

/** The app's own authenticated call: GET without a body, POST with one. */
export type Call = <T>(path: string, body?: unknown) => Promise<T>;

export interface TwoStepApi {
  status: () => Promise<TwoStepStatus>;
  start: (password: string) => Promise<{ secret: string; link: string }>;
  confirm: (code: string) => Promise<string[]>;
  off: (password: string, code: string) => Promise<void>;
  newCodes: (code: string) => Promise<string[]>;
}

/** The plain-words message for a failed two-step call (each app has its own ApiError class). */
export function friendlyTwoStepError(e: unknown): Error {
  const err = e as { code?: string; fields?: string[] } | null;
  if (err?.code === "VALIDATION_FAILED") return new Error("That did not work. Check what you typed (codes change every 30 seconds) and try again. Five wrong tries lock the account for 15 minutes.");
  if (err?.code === "POLICY_DENIED") return new Error("Your organization requires two-step sign-in for staff, so it cannot be turned off.");
  if (err?.code === "CONFLICT_VERSION") return new Error("This changed in another window. Reload the page.");
  if (err?.code === "NOT_FOUND") return new Error("A development link has no password login. Sign in with your login and password to set this up.");
  return e instanceof Error ? e : new Error("Something went wrong");
}

/** `setToken` swaps in the fresh session the server returns when it is switched on or off (every
 *  other device is signed out then). */
export function makeTwoStepApi(call: Call, setToken: (token: string) => void): TwoStepApi {
  const wrap = async <T,>(run: () => Promise<T>): Promise<T> => {
    try { return await run(); } catch (e) { throw friendlyTwoStepError(e); }
  };
  return {
    status: () => wrap(async () => (await call<{ two_step: TwoStepStatus }>("auth/two-step")).two_step),
    start: (password) => wrap(() => call<{ secret: string; link: string }>("auth/two-step/set-up", { password })),
    confirm: (code) => wrap(async () => {
      const r = await call<{ token: string; recovery_codes: string[] }>("auth/two-step/confirm", { code });
      setToken(r.token);
      return r.recovery_codes;
    }),
    off: (password, code) => wrap(async () => {
      setToken((await call<{ token: string }>("auth/two-step/off", { password, code })).token);
    }),
    newCodes: (code) => wrap(async () =>
      (await call<{ recovery_codes: string[] }>("auth/two-step/recovery-codes", { code })).recovery_codes),
  };
}

/** The second step of sign-in: the ticket from the password step, and a code. Resolves to the
 *  same reply a one-step sign-in gives. */
export async function finishSignIn<T>(ticket: string, code: string): Promise<T> {
  const res = await fetch("/api/v1/auth/login/second-step", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticket, code }),
  });
  if (!(res.headers.get("Content-Type") ?? "").includes("json")) throw new Error("Cannot reach the server");
  if (res.status === 401) {
    throw new Error("That code did not work, or the 5 minutes ran out. Try a new code, or start again. Five wrong tries lock the account for 15 minutes.");
  }
  const body = (await res.json()) as T & { title?: string };
  if (!res.ok) throw new Error(body.title ?? `Sign-in failed (${res.status})`);
  return body;
}

/** Is this answer "the clinic requires two-step and you have not set it up"? */
export function needsSetUp(status: number, body: { code?: string; fields?: string[] }): boolean {
  return status === 401 && body.code === "STEP_UP_REQUIRED" && (body.fields ?? []).includes("two_step_set_up");
}

export const SET_UP_FIRST = "Your organization requires two-step sign-in. Open My account and set it up first.";

/** Before a risky action the server may ask for the code again (STEP_UP_REQUIRED, field
 *  "two_step"). Asks the person, checks it, and returns the fresh session token, or null when
 *  they cancel or it is not that answer. The caller then retries its request once. */
export async function recheckIfAsked(
  status: number, body: { code?: string; fields?: string[] }, token: string,
): Promise<string | null> {
  if (!(status === 401 && body.code === "STEP_UP_REQUIRED" && (body.fields ?? []).includes("two_step"))) return null;
  let note = "";
  for (;;) {
    const code = await askForCode(note);
    if (code === null) return null;
    const res = await fetch("/api/v1/auth/two-step/recheck", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ code }),
    });
    if (res.ok) return ((await res.json()) as { token: string }).token;
    if (res.status !== 422) return null;
    note = "That code did not work. Codes change every 30 seconds. Five wrong tries lock the account for 15 minutes.";
  }
}

/** A small modal asking for the 6-digit code (or a recovery code). Built without React so any
 *  app's API layer can use it. Resolves to the code, or null if cancelled. */
export function askForCode(note = ""): Promise<string | null> {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.setAttribute("aria-labelledby", "two-step-title");
    dialog.style.cssText = "border:1px solid var(--line,#ccc);border-radius:14px;padding:20px;max-width:380px;width:calc(100% - 32px)";
    const form = document.createElement("form");
    form.method = "dialog";
    const title = document.createElement("h3");
    title.id = "two-step-title";
    title.textContent = "Enter your code to continue";
    title.style.marginTop = "0";
    const text = document.createElement("p");
    text.textContent = "This is a sensitive action. Type the 6-digit code from your authenticator app, or a recovery code.";
    const input = document.createElement("input");
    input.className = "field";
    input.autocomplete = "one-time-code";
    input.inputMode = "numeric";
    input.setAttribute("aria-label", "Code");
    input.required = true;
    const buttons = document.createElement("div");
    buttons.style.cssText = "display:flex;gap:8px;margin-top:12px";
    const ok = document.createElement("button");
    ok.className = "btn"; ok.type = "submit"; ok.textContent = "Continue";
    const cancel = document.createElement("button");
    cancel.className = "btn ghost"; cancel.type = "button"; cancel.textContent = "Cancel";
    buttons.append(ok, cancel);
    form.append(title, text);
    if (note) {
      const warn = document.createElement("div");
      warn.className = "error"; warn.setAttribute("role", "alert"); warn.textContent = note;
      form.append(warn);
    }
    form.append(input, buttons);
    dialog.append(form);
    let answer: string | null = null;
    form.addEventListener("submit", () => { answer = input.value.trim() || null; });
    cancel.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => { dialog.remove(); resolve(answer); });
    document.body.append(dialog);
    dialog.showModal();
    input.focus();
  });
}
