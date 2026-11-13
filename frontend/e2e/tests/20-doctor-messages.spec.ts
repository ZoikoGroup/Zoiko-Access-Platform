import { expect, test } from "@playwright/test";
import { API, LOGINS, apiToken, bearer, signIn } from "../helpers";

// Robot 20: a patient writes to the clinic and the doctor who cares for them reads and answers it in
// the doctor app. The patient then sees the answer as the clinic's. A doctor sees only their own
// patients' conversations (the test clinic links this doctor to this patient).
test("a doctor reads a patient's message and answers it", async ({ page, request }) => {
  const subject = `Robot question ${Date.now()}`;
  const patient = bearer(await apiToken(request, LOGINS.patient));
  const made = await request.post(`${API}messages/threads`, {
    headers: patient, data: { subject, body: "Can I move my visit to next week?" },
  });
  expect(made.status()).toBe(201);
  const thread = (await made.json()).thread.id as string;

  await signIn(page, "doctor", LOGINS.doctor);
  await page.locator(".nav-item", { hasText: "Messages" }).click();
  await expect(page.getByTestId("inbox-status")).toHaveAttribute("aria-live", "polite");
  const row = page.locator(".appt", { hasText: subject });
  await expect(row).toBeVisible();
  await expect(row.getByText(/\d+ new/)).toBeVisible();
  await row.getByRole("button", { name: "Open" }).click();
  await expect(page.getByText("Can I move my visit to next week?")).toBeVisible();
  await page.getByLabel("Your reply").fill("Yes, next Tuesday works.");
  await page.getByRole("button", { name: "Reply" }).click();
  await expect(page.getByLabel("Your reply")).toHaveValue("");           // the send has finished
  await expect(page.locator(".appt", { hasText: "Yes, next Tuesday works." })).toBeVisible();

  const messagesSeenByPatient = async () => {
    const seen = await request.get(`${API}messages/threads/${thread}`, { headers: patient });
    return (await seen.json()).thread.messages as { body: string; side: string }[];
  };
  await expect.poll(async () => (await messagesSeenByPatient()).map((m) => m.body))
    .toEqual(["Can I move my visit to next week?", "Yes, next Tuesday works."]);
  expect((await messagesSeenByPatient())[1].side).toBe("staff");     // the patient sees the clinic answering
});
