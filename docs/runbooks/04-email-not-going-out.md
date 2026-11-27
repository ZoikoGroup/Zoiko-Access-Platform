# 04: Email not going out

**Signs:** the platform overview's mail numbers show "failed" or "held" growing; people say their
password-reset or invitation emails do not arrive.

## Check
1. **Held** emails: their template version is not approved (DEV-386). Platform console, **Message
   templates**: a safety officer and, separately, privacy must approve it. Held emails are sent
   the moment it is approved.
2. **Failed** emails: the worker gave up after 5 tries. The logs show the error class only.
3. Is the worker running? (`docker compose ps worker`.)
4. Is the provider (SendGrid) up, and is `SENDGRID_API_HOST` one of its own hosts?
5. Delivery receipts: "bounced" or "dropped" for one address is about that address, not an
   outage.

## Act
- Approve the waiting template (two people), restart the worker, or wait for the provider.
- Do not resend failed one-time links by hand: ask the person to request a new one.

## Done when
No new failures for 30 minutes, and the queue is empty or draining.
