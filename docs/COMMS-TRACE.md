# Secure communications: the specification's 64 acceptance cases, checked against what is built

Written 2026-11-12 by the developer, from the specification's own acceptance cases. Each row is a judgement made by reading the case and the code, not an automatic result; "Met" means one of our own automated tests proves the behaviour. The cases have **not** been run as written. This is the developer's reading and nobody else has reviewed it.

**Totals: 3 met, 27 partly, 34 not built (of 64).**

What exists is in-app messaging between a patient and their clinic, email through a development inbox, a daily staff digest, and the evidence rules of the workflow engine. The "not built" rows are the real channels (SMS, push, phone), vendors, message templates and approvals, language and right-to-left support, emergency escalation channels and the FHIR communication mapping, which need a provider, a market decision and clinical sign-off.

| Case | What the specification tests | Status | Why, and where it is tested |
|---|---|---|---|
| TC01 | Send patient content from a non-activated market | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC02 | Submit intent without valid region binding | Partly | Every request needs a signed-in token that fixes the customer; no token, no request. Regions are not modelled. |
| TC03 | Attempt a cross-cell thread lookup with valid tenant login | Partly | One customer can never read another's threads (test_rls.py, test_messaging.py). There is one cell. |
| TC04 | Use provider credential for another patient relationship | Partly | A doctor reaches only the patients they have a care link with (test_clinician.py, test_site_patients.py). |
| TC05 | Try to send C2 body through public SMS channel | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC06 | Load content in global analytics/control plane | Partly | Metrics labels hold route patterns only, never content (test_metrics.py). No analytics or control plane exists. |
| TC07 | Change country pack to expired/unsigned revision | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC08 | Unapproved overseas backup vendor failover | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC09 | Deliver to a phone not verified for intended patient | Partly | Staff digest emails go only to verified addresses (test_digest.py). There is no phone channel. |
| TC10 | Revoke proxy after notification dispatch before open | Partly | A guardian's access is checked live on every request, so a revoked guardian is refused at once (test_guardian_scope.py, test_grant_states.py). Notification-open is not modelled. |
| TC11 | Change clinician team membership during work item pending | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC12 | Employer requests employee-level message export | Partly | The employer role has no right to messages under the default-deny rules (test_prohibitions.py). No export exists to test. |
| TC13 | Send generic push to shared lock-screen device | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC14 | Try using opaque message ID from other patient | Partly | Public ids are random and every read is checked against the signed-in patient (test_messaging.py, test_ids.py). |
| TC15 | Suspected phone recycling flag set | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC16 | Mass send with one unapproved recipient in batch | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC17 | Submit same idempotency key and exact payload twice | Met | A message or reply sent twice with one key posts once and returns the same thread (test_message_idempotency.py). |
| TC18 | Submit same key with different payload | Met | The same key with different words is refused with 409 and nothing new is made (test_message_idempotency.py). |
| TC19 | Vendor sends signed callback with invalid signature | Partly | The signed-message mechanism refuses a bad signature (test_partner_events.py), but there is no communications vendor. |
| TC20 | Callback replay with reused nonce | Partly | A repeated delivery id is stored once and a reused id with new content is refused (test_partner_events.py); no vendor callbacks. |
| TC21 | Receipt arrives out of order / delayed | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC22 | Vendor times out then delivers previously submitted message | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC23 | Provider rejects payload with unsafe text | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC24 | Attempt cancellation after irreversible external delivery | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC25 | Provider adapter reports HTTP 200 for a critical task | Partly | test_orchestration.py: a transport acknowledgement is refused as evidence of a clinical step. No provider adapter exists. |
| TC26 | Clinician opens result without explicit sign-off | Partly | test_orchestration.py: a viewed or read receipt is refused as evidence of review. There are no clinical results. |
| TC27 | Unlicensed/admin user attempts reviewed attestation | Met | test_orchestration.py: an administrator cannot enter a clinical review or handoff for the accountable owner. |
| TC28 | Primary clinical alert timer expires | Partly | test_orchestration.py: an expired timer opens an escalation for the named owner. No message is sent. |
| TC29 | Backup contact unreachable | Partly | test_orchestration.py: an unsuccessful try is counted and can hand the escalation to a fallback person. |
| TC30 | Quiet hours suppress routine reminder | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC31 | Clinician declines handoff | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC32 | Safety plan absent/unapproved | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC33 | Patient inbox keyboard navigation at 320 CSS px | Partly | The patient inbox passes the accessibility scan at 320 pixels wide with no sideways scrolling, and the menu works from the keyboard (browser robot 17). A person on a real phone must still try it. |
| TC34 | Screen reader receives new-message status | Partly | The patient inbox has a polite live region that says how many new messages there are, so a screen reader reads it when the count changes (browser robot 18). There is no push or other notification channel, and no real screen reader was used. |
| TC35 | User selects right-to-left locale | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC36 | Critical template translation lacks approval | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC37 | User updates preferred channel during pending send | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC38 | Expired link or invalid deep link | Partly | Verification and reset links expire and work once (test_email.py, test_change_password.py). |
| TC39 | No usable digital channel / disability access needs | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC40 | Timezone passes DST skip/repeat | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC41 | Regional communications worker crashes after outbox write | Partly | Mail is queued in the database before it is sent and events are at-least-once (test_outbox.py, test_email.py). No crash is simulated. |
| TC42 | Vendor circuit breaker opens | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC43 | Dead-letter queue exceeds threshold | Partly | A webhook delivery that gave up stays visible, can be sent again, and is counted on the administrator's dashboard (test_webhooks.py). There is no threshold that raises an alert. |
| TC44 | Regional KMS unavailable | Partly | If a key is unavailable, decryption fails safely with no fallback. Nothing reports it as degraded. |
| TC45 | On-call roster becomes stale | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC46 | Breach candidate: wrong-recipient report | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC47 | Recovery replays old webhook events | Partly | Replayed partner deliveries are stored once (test_partner_events.py). There is no recovery replay. |
| TC48 | Failover proposed to unapproved geographic zone | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC49 | Unapproved template variable introduced | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC50 | New template approved by only marketing | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC51 | Patient withdraws optional marketing consent | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC52 | Notification body appears in APM trace | Partly | Metrics and logs hold no message bodies (test_metrics.py, test_errors.py). There is no tracing tool. |
| TC53 | Staff requests message evidence without purpose | Partly | Doctors state a purpose that the rules check and the audit records (test_purpose.py, test_audit_who.py). Staff message evidence is not a separate feature. |
| TC54 | Legal hold prevents immediate erasure | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC55 | Small employer cohort aggregate query | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC56 | Partner vendor subprocessor changed unapproved | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC57 | Map FHIR CommunicationRequest to dispatched intent | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC58 | Import partner Communication delivery fact | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC59 | Connector loses source event correlation | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC60 | Role transition in provider roster | Partly | A doctor who leaves a site loses that site's patients and inbox (test_clinician.py, test_site_patients.py). |
| TC61 | Deploy service with no approved safety evidence | Partly | test_readiness.py: a release is not ready without its signed requirements. It records a decision and blocks nothing. |
| TC62 | Simulate all urgent routes unavailable | Not built | There is no real communications channel (SMS, push or phone), vendor, template approval or regional routing in this project yet. |
| TC63 | Test downgrade of approved country/channel policy | Partly | test_country_deadline.py, test_readiness.py: suspending a pack removes what it gave and the release's authorisation. |
| TC64 | Review signed audit chain after corrections/cancellations | Partly | The audit chain is checked end to end and corrections are new entries (test_audit.py). |
