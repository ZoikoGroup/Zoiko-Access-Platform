# Identity, patient matching and access: the specification's 40 acceptance cases, checked against what is built

Written 2026-11-12 by the developer, from the specification's own acceptance cases. Each row is a judgement made by reading the case and the code, not an automatic result; "Met" means one of our own automated tests proves the behaviour. The cases have **not** been run as written. This is the developer's reading and nobody else has reviewed it.

**Totals: 7 met, 13 partly, 20 not built (of 40).**

What exists is per-customer sign-in, roles with default-deny rules, guardians with scoped and revocable access, doctors tied to a clinic site, two-person suspension of a customer, and an audit trail. The "not built" rows are patient matching and merging, break-glass emergency access, single sign-in and licence checks, OAuth purposes, cross-cell consent and the identity-proofing steps, which need a matching decision, an identity provider and clinical sign-off.

| Case | What the specification tests | Status | Why, and where it is tested |
|---|---|---|---|
| AT-01 | Email verified but person not clinically proofed | Partly | A verified email does not make a person proofed: guardians and family links need a staff check (test_family.py). Identity proofing itself is not built. |
| AT-02 | Recovery through compromised contact | Partly | Account recovery is a single-use, one-hour link to the verified email (test_email.py, test_change_password.py). A compromised contact is not detected. |
| AT-03 | Token issued to wrong audience or expired | Met | test_auth.py: an expired, tampered or foreign-key token is refused. |
| AT-04 | Clinician SSO success with inactive regional license | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-05 | Workload token used as human proxy | Partly | A partner's credential can only post signed partner messages and cannot act as a person (test_partner_events.py). |
| AT-06 | Foreign global account presents valid login | Partly | A login belongs to one customer and a token fixes it (test_auth.py, test_rls.py). There are no global accounts. |
| AT-07 | Same name and DOB for twins | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-08 | Swapped identifiers from lab partner | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-09 | No-match patient registers successfully | Partly | A patient registers and gets their own new record (test_auth.py). There is no matching to test the no-match case against. |
| AT-10 | High probabilistic score but source rejects assignment | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-11 | Patient ID from incorrect assigning authority | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-12 | Erroneous historical merge discovered | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-13 | Duplicate result arrives while adjudication pending | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-14 | Reviewer tries approve own critical match decision | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-15 | Guardian lacks verified legal basis for minor | Partly | A guardian is approved by a staff member who must say what kind of proof they saw and where it is kept; both are recorded and audited (test_family.py). The legal basis is still the staff member's word: nothing here verifies the document or ties a kind of proof to a relationship. |
| AT-16 | Minor age threshold changes under market rule | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-17 | Patient revokes caregiver with active session | Partly | A guardian's access is checked live on every request, so revoking takes effect at once (test_grant_states.py, test_guardian_scope.py). |
| AT-18 | Proxy allowed scheduling but requests notes | Met | test_guardian_scope.py: a guardian sees only the parts the clinic granted (appointments, messages, referrals, insurance) and never records. |
| AT-19 | Employer sponsor requests individual lab result | Met | test_prohibitions.py: the employer role is barred from patient data whatever else is true. |
| AT-20 | Clinician moves facilities | Met | test_clinician.py: a doctor's care link is tied to the clinic site and ends when they leave it. |
| AT-21 | Facility license or provider contract suspended | Met | test_suspensions.py: a suspended customer's staff are refused at once, and it takes two different support people. |
| AT-22 | Nurse has staff role but no current care assignment | Met | test_site_patients.py: staff reach only the patients who have dealings with their own site. |
| AT-23 | Support agent tries emergency clinical access | Met | test_prohibitions.py: support is barred from patient data and messages, and no emergency access exists. |
| AT-24 | Approved OAuth scope with missing lawful-purpose flow | Partly | A doctor states a purpose that the rules check against their role (test_purpose.py). There is no OAuth. |
| AT-25 | Revocation store unavailable | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-26 | Old country pack with broader policy than current | Partly | A suspended or expired pack stops applying (test_packs.py, test_country_deadline.py); packs are not otherwise used. |
| AT-27 | Cross-tenant user reuses patient link | Partly | Customers are separated by row-level security (test_rls.py, test_db_role.py). |
| AT-28 | Patient can be inferred by query timing or error text | Partly | Every login failure is the same answer (test_auth.py) and another customer's object is 'not found', never 'forbidden'. |
| AT-29 | Overseas support session without transfer authorization | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-30 | Cross-cell referral missing source consent/authority where relevant | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-31 | Source record permission revoked after workflow queued | Partly | Each workflow step re-checks the actor's rights as it is taken (test_orchestration.py). |
| AT-32 | Unapproved regional instance receives global tenant metadata | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-33 | Break-glass called in nonactivated country | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-34 | Enabled emergency request lacks reason/step-up | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-35 | Emergency session expires before clinical action | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-36 | Audit write or evidence integrity check fails | Partly | Audit rows are written in the same transaction as the change they describe (test_audit.py), so a failure undoes the change. |
| AT-37 | License revocation occurs during message delivery | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-38 | Identity provider outage during urgent care | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-39 | Unauthorized reviewer exports identity-match evidence | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
| AT-40 | Patient challenges identity association | Not built | Patient matching, break-glass emergency access, single sign-on and the other identity-assurance machinery of this specification are not built. |
