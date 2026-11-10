# Zoiko Access: eight decisions that unblock the most

**For:** the lead, and the owners of Legal, Privacy, Clinical Safety and Engineering.
**Date:** 7 November 2026.
**Length:** a five-minute read. The full list of 26 questions is in `docs/DECISIONS-NEEDED.md`; the technical reasoning is in `docs/decisions/REGISTER.md`.

## Where we are

The developer has built everything that can be built without these answers. Patients, clinics, doctors and Zoiko staff each have their own app. A patient can find care, book, move or cancel, join a waiting list, get reminders, message the clinic, manage a family member's care and keep insurance details on file. Clinics confirm bookings, record who attended, work from a queue of what is waiting, and see reports. Zoiko staff can add a customer, open or close its patient sign-up, suspend it (two different people must agree), and keep a release log and an incident log. Every step of a booking and of a work item is kept in a tamper-evident history, and the app refuses to start on a database login that would let one customer's data leak into another's.

It has been tested well (a large automated test suite; the last full run on 7 November counted 1,494 passing and 1 skipped, and the 8 tests that failed there (they assumed the old rules) were fixed and rerun) and run only on a developer's computer. **Nobody has reviewed it** for security, privacy or clinical safety, and no real user has tried it. What is left cannot be built honestly until people with the authority decide the points below.

## The eight decisions

For each: who decides, what it unlocks, and a **suggestion**. The suggestions are the developer's engineering defaults to make the conversation quicker. They are not legal, clinical or commercial advice: the named owner should confirm or replace them.

| # | Decision | Owner | What it unlocks | Suggested default |
|---|---|---|---|---|
| 1 | **First market, first buyer, first journey** (DEV-011) | Board, Commercial | Which laws apply, which language and time zone, which features come first | One country, one clinic customer, the journey we already built: *book and attend a visit* |
| 2 | **Zoiko's legal role** (DEV-012): infrastructure, marketplace or provider | Legal | What Zoiko may store, say and be liable for | A **coordination platform**: it arranges visits and holds no medical advice. The app already says so on screen |
| 3 | **May Zoiko store clinical notes and results at all?** (DEV-041) | Board, Legal, Privacy | Whether the doctor app can grow past "my patients" and the referral list | **No, for the first release.** Doctors keep records in their own system. This removes the heaviest safety and privacy burden |
| 4 | **Hosting and encryption keys** (12 and 13 on the full list) | CTO, Security | Everything about going live. The database part of the setup is already written and rehearsed (`docs/DEPLOYMENT.md`) | One managed PostgreSQL service in the chosen market's region, with a managed key service. Losing the key means the encrypted details are unreadable for ever, so decide who holds the recovery copy |
| 5 | **How long data is kept, and what a patient may control** (6 and 7) | Legal, Privacy | Today nothing is ever deleted and consent is not built | Keep the current role-based access for the first release. Legal sets the retention periods; the developer then makes them a per-customer setting |
| 6 | **Real email and what messages may say** (21, 9 and 24) | Product, Privacy, Clinical Safety | Today a development inbox catches all mail. Real reminders and verification emails need a provider | Pick a provider. Keep emails generic (no care details), as now. Clinical Safety writes the emergency wording and the number per country |
| 7 | **Who verifies a doctor's name and registration** (25) | Clinical Safety, Legal | Today a doctor types their own name, shown as unverified. A public doctor listing is blocked on this | The customer's administrator attests each doctor, and a name is shown as verified only after that |
| 8 | **How insurers take part** (3) | Commercial, Legal | Claims, billing and an insurer app | **Out of the first release.** Patients can still record which insurance they hold |

If you can answer only three: **2, 3 and 4**. Items 2 and 3 decide whether a large part of the remaining product is allowed to exist, and item 4 decides whether anything can be put in front of real patients.

## What has not been checked by anyone

- A **security review** or penetration test.
- A **privacy review** (data protection impact assessment).
- A **clinical-safety review**.
- **Real users** trying the screens, accessibility checking, translation.
- Performance with real numbers of patients.
- The screens have **not been clicked through in a browser** by a person since the last round of features, and a clean rebuild of the Docker image from scratch has failed twice on network timeouts.

Booking the first three reviews early is the single best use of time while the decisions above are made.

## What the developer has just finished without any decision

- A **release check**: production refuses to start while any development-only setting is still on (the unsigned test sign-in, short demo passwords, placeholder secrets, local-only host names, the development inbox). `python manage.py check --deploy` lists them. Item 15 on the full list, now closed except for the key-management and mail-provider decisions above.

## AI: built switched off, three questions for you

The developer built the safety rules the documents set for AI (docs/schema file 05, section G), and nothing else. An AI feature cannot run without a permit signed by two people, tools are whitelist-only, anything the AI wants to change waits for a human, and no patient text is stored. The only "agent" is a fixed polite reply template for staff to review; it uses no outside model (register entry DEV-093). Three things need you:

1. **Who may create and approve AI permits?** Today no role or screen exists, so nothing can switch AI on. The documents name four sign-offs (Legal, Clinical, QA, SRE); we have two people minimum for now.
2. **May Zoiko use an outside AI model at all?** If yes: which provider, under what contract, in which region, and with what privacy review. Until then no patient data can go to one.
3. **Which AI help, if any, comes first?** The safest start is admin help (drafting a reply for staff to read and send). Anything touching clinical content needs a clinical owner and a safety review.

## Found by checking the code against the original documents (see docs/CONFORMANCE-AUDIT.md)

Seven gaps were found. The developer did not leave them open: each is **already built on a proposed answer**, marked temporary in the decision register, so the product works today. **Please confirm or change each proposal.** If you change one, the matching change is small. In plain words:

| # | Question | What is built now (the proposal) | Register | Your answer |
|---|---|---|---|---|
| 1 | Which patients may a clinic's staff see? | Only patients who have a booking, a referral, a waiting-list entry, a message or a family request at that site, or a doctor there treating them. | DEV-107 | Confirm, or say what "minimal" means. |
| 2 | How long may a doctor keep access after a visit, and does it follow them to another site? | The link belongs to the clinic site and ends the moment the doctor leaves it. The **30 days** after the visit is still our own number. | DEV-106 | Give the real number (or none). |
| 3 | What may a guardian see? | The clinic chooses when approving: appointments, messages, referrals, insurance. Medical records stay closed for every guardian. | DEV-110 | Confirm the four, say who may unlock records and what proof is needed (and age or capacity rules). |
| 4 | When a patient moves or cancels a confirmed appointment, does the old one stay until the clinic answers? | Yes. The patient only asks; the original stays booked and on the doctor's schedule until the clinic confirms the new time or the cancellation. | DEV-108 | Confirm; say who answers, whether there is a deadline, and whether a patient may take a request back. |
| 5 | Who owns an incoming referral, and who is the fallback? | Each clinic site names a doctor to answer referrals and a different fallback. Staff can only mark "received". An answer nobody gives in 24 hours is flagged and goes to the fallback; nothing closes by itself. | DEV-109 | Confirm; give the real time limit and what escalation should do (message someone?). |
| 6 | Must a user state a reason when they open a patient's record? | A doctor chooses "treat my patients" or "follow up earlier care (read only)"; it is checked against the role, recorded and shown to the patient. | DEV-111 | Confirm; say whether a reason per patient is wanted, and what extra the follow-up purpose may do. |
| 7 | Which role may exist in which kind of customer? | Partly: the employer and payer roles can only go to employer and payer logins, which can hold nothing else. | DEV-105 | Give the full table of roles per customer type. |

Two things the developer could not decide alone and did not guess: **proof of the legal relationship for a guardian**, and **who may authorize AI permits** (question 1 above).

## What happens next

Send this note. When your answers come back, the developer changes the proposals that differ, in the order you give. Until then nothing is blocked: everything above works and is tested; it is simply not yet confirmed by you or by a clinical-safety owner.
