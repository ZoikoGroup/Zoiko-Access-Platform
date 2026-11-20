# Go-live checklist: first UK clinic, real patients

Everything that has to be true before real patients use Zoiko Access, in order. Each line says **who**
does it and **how we know it is done**. The technical steps are in
[DEPLOYMENT-GCP.md](DEPLOYMENT-GCP.md); this list adds the people, accounts and sign-offs around them.
Tick a box only when its "done when" is true.

## 1. Accounts and agreements (business owner)

- [ ] **Google Cloud project with billing**, in the company's name. *Done when:* project id and billing account exist.
- [ ] **SendGrid account** (email, DEV-350). Verify the sending domain (SPF, DKIM). Create an API key with "Mail Send" only. Turn on the **signed Event Webhook** and note its verification key (DEV-354). *Done when:* domain shows "verified".
- [ ] **Twilio account** (texts, DEV-194). Buy a UK sender number. *Done when:* account SID, auth token and number are known.
- [ ] **The first clinic** has signed its customer agreement and data-processing agreement. *Done when:* both are signed.
- [ ] **A partner lab** (if results are in scope for go-live) has signed and given a test system. *Done when:* agreement signed and test endpoint known.

## 2. Reviews that must be signed (named reviewers)

- [ ] **Clinical safety (DCB0129):** clinical safety case and hazard log reviewed and signed by a Clinical Safety Officer. *Done when:* signed case, with its reference.
- [ ] **Data protection:** DPIA for the first release, signed by the DPO. *Done when:* signed DPIA.
- [ ] **Security:** penetration test of the deployed system, findings fixed or accepted in writing. *Done when:* report plus written closure.
- [ ] **The deployment plan** (`terraform plan`) reviewed by a second engineer. *Done when:* reviewer named in the release note.

## 3. Build the cell (engineer, with [DEPLOYMENT-GCP.md](DEPLOYMENT-GCP.md))

- [ ] Steps 1 to 7 of "First deployment, in order". *Done when:* the migrate job succeeds and `check --deploy` passes.
- [ ] **Secrets in Secret Manager** (never in files): `email-password` (any value; SMTP is only a fallback), `twilio-auth-token`, **`sendgrid-api-key`**. Then `manual_secrets_ready = true` and apply. *Done when:* the web service starts.
- [ ] **SendGrid webhook key** in `sendgrid_webhook_public_key`, and the Event Webhook in SendGrid pointed at `https://api.<domain>/api/v1/webhooks/sendgrid/events`. *Done when:* a test email shows "delivered" in the platform's mail numbers.
- [ ] **Virus scanner** (ClamAV VM, DEV-351) running. *Done when:* uploading the standard EICAR test file as a lab PDF is marked "virus found", and a clean PDF opens for the treating doctor.
- [ ] Steps 9 to 11: reference data, the five apps, the domain. *Done when:* every host loads over HTTPS and the uptime check is green.

## 4. Switch things on (in this order, each by its named owner)

- [ ] **UK country pack** signed by a legal and a clinical reviewer. Texts are sent only in a market whose pack switches messaging on (DEV-228). *Done when:* the pack shows "signed, current".
- [ ] **Clinical notes**, only after the clinical safety sign-off in section 2: set `ZOIKO_CLINICAL_NOTES=true` **and** `ZOIKO_NOTES_SAFETY_SIGNOFF=<the signed case's reference>`. The app refuses to start with one and not the other (DEV-324). *Done when:* a doctor can sign a note and the patient sees it the next day.
- [ ] **The partner lab**, if in scope: enrol (support), certify (safety officer), put live (a third person) (DEV-230). Before certifying, run the **official HL7 FHIR validator** on the lab's real sample messages (DEV-355). *Done when:* the lab shows "live" and a test order and result go end to end.
- [ ] **The first clinic's staff accounts** created by its administrator; at least **two** administrators (two-person rules: deaths, adult attorneys). *Done when:* both can sign in.

## 5. First day checks (engineer and clinic lead, together)

- [ ] A patient registers, books, and the clinic confirms. Reminder email and text arrive.
- [ ] A doctor writes and signs a note, places an order, reviews a result (if lab is live).
- [ ] Zoiko support sees "Delivery attempts" with nothing stuck, and "Code mapping" with nothing unexpected.
- [ ] No errors in Cloud Logging beyond known warnings; the uptime check stays green for 24 hours.

## 6. Known gaps accepted for go-live (record who accepted each)

From [DEPLOYMENT-GCP.md](DEPLOYMENT-GCP.md) "Not covered yet": no scheduled database export or timed restore drill on the real database; one region only; no WAF (Cloud Armor) or cost alerts; default log retention. Each needs a named person to accept it, or to be done first.
