/** The second step of signing in, for every app (DEV-356). When the server answers a sign-in with
 *  "a code is owed", this asks for it in a small dialog and gives back the finished sign-in. Plain
 *  DOM, so any app's sign-in function can use it without changing its screen. */

interface Owed { second_factor_required: true; challenge: string }

const isOwed = (body: unknown): body is Owed =>
  typeof body === "object" && body !== null && (body as Owed).second_factor_required === true;

function element<K extends keyof HTMLElementTagNameMap>(tag: K, text = "", className = ""): HTMLElementTagNameMap[K] {
  const made = document.createElement(tag);
  if (text) made.textContent = text;
  if (className) made.className = className;
  return made;
}

function askForCode<T>(challenge: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const overlay = element("div", "", "second-step");
    const box = element("form", "", "second-step-box");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "second-step-title");
    const title = element("h2", "Enter your code");
    title.id = "second-step-title";
    const label = element("label", "Six digits from your authenticator app, or a recovery code");
    const input = element("input");
    input.type = "text";
    input.autocomplete = "one-time-code";
    input.maxLength = 32;
    input.required = true;
    label.appendChild(input);
    const problem = element("div", "", "second-step-problem");
    problem.setAttribute("role", "alert");
    const row = element("div", "", "second-step-actions");
    const ok = element("button", "Verify", "btn");
    ok.type = "submit";
    const cancel = element("button", "Cancel", "btn ghost");
    cancel.type = "button";
    row.append(ok, cancel);
    box.append(title, label, problem, row);
    overlay.appendChild(box);
    const close = () => overlay.remove();
    const giveUp = () => { close(); reject(new Error("Sign-in was cancelled.")); };
    cancel.addEventListener("click", giveUp);
    overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") giveUp(); });
    box.addEventListener("submit", (e) => {
      e.preventDefault();
      ok.disabled = true;
      problem.textContent = "";
      fetch("/api/v1/auth/second-factor/verify", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ challenge, code: input.value.trim() }),
      }).then(async (res) => {
        if (res.ok) { close(); resolve((await res.json()) as T); return; }
        problem.textContent = res.status === 401
          ? "That code did not work. Codes work once; wait for the next one. After several wrong codes, sign-in is paused for a while."
          : "Could not check the code. Try again.";
        ok.disabled = false;
        input.select();
      }).catch(() => { problem.textContent = "Cannot reach the server."; ok.disabled = false; });
    });
    document.body.appendChild(overlay);
    input.focus();
  });
}

/** Give a sign-in answer; get back a finished one, asking for the code first if one is owed. */
export async function finishSignIn<T>(body: unknown): Promise<T> {
  return isOwed(body) ? askForCode<T>(body.challenge) : (body as T);
}
