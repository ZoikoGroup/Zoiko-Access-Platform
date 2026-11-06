# Zoiko Access: decisions we need from the business

**For:** the lead and the owners of each area below.
**Why this exists:** the developer has built everything that could be built without these answers. What is left cannot be built honestly until a person with the authority decides. Each item says what we did meanwhile, so nothing is hidden. The full technical reasoning is in `docs/decisions/REGISTER.md` (the DEV numbers).

**What is working today (development only):** patients, clinics, doctors and Zoiko staff each have their own app. A patient can find care, book, move or cancel, join a waiting list, get reminders, message the clinic, manage a family member's care, keep their details and insurance on file, see who looked at their information, see their referrals and each appointment's history. Clinics confirm bookings, record attendance, work from a queue of what is waiting, see reports, manage their team and send referrals. Zoiko staff can add a customer by invitation, open or close its patient sign-up, suspend it (two different people must agree), and keep a release log and an incident log. Patient details and insurance numbers are stored encrypted, and the app refuses to start on a database login that could leak one customer's data to another. (A five-minute summary of the eight decisions that unblock the most is in `docs/LEAD-NOTE.md`.) Nothing has been reviewed by a lead, a privacy owner or a clinical-safety owner, and it has not been run anywhere but a developer's computer.

---

## 1. Business and legal (Board, Commercial, Legal)

| # | Decision | Why it matters | What we did meanwhile |
|---|---|---|---|
| 1 | **First market, first buyer, first journey** (DEV-011) | Decides which features matter first and which laws apply. | We built the "book and attend a visit" journey only to learn. |
| 2 | **Zoiko's legal role** per service: infrastructure, marketplace, or provider (DEV-012) | Decides what Zoiko may store, say and be liable for. | We built it as a coordination platform and said so on screen. |
| 3 | **How insurers take part** (DEV-049): are they customers with their own login, how a policy is checked, who pays whom | Without this there can be no insurer app, no claims and no billing. | Patients and the front desk can only record which insurance a patient holds (unverified). |
| 4 | **May Zoiko store clinical notes and results at all?** (DEV-041) | Storing them would make Zoiko hold medical records. | Not built. The doctor app says "soon". |
| 5 | **Who approves a new customer** | Today one Zoiko support login can add a customer alone. The docs call for two people on tenant-level actions. | Not built for adding a customer. Suspending or reinstating a customer already takes two different people (DEV-080; see item 26). |

## 2. Privacy and data protection (Privacy, DPO, Legal)

| # | Decision | Why it matters | What we did meanwhile |
|---|---|---|---|
| 6 | **How long data is kept**, key rotation, and where data may go (DEV-013) | Retention is a legal duty and cannot be guessed. | Nothing is ever deleted; no rule exists. |
| 7 | **Consent model**: who may see what, and how a patient changes it | Today access follows roles and care relationships only. The patient app says consent is not built. | Patients can see who looked at their information, but cannot change it. |
| 8 | **How long a doctor can see a patient after a visit** | We chose 30 days after the visit. That number is ours, not from the documents. | Fixed at 30 days. |
| 9 | **What a notification or a reminder may say**, and whether email or SMS may carry appointment details | An appointment can reveal a health condition. | Reminders and notices appear in the app. Emails (to a development inbox only) carry generic wording and no care details, and a patient can switch them off. No SMS. |
| 10 | **What staff names may be shown to patients** | The access log shows staff by their login (an email address). | Shown as logins. |
| 11 | **Who may register themselves** (DEV-031) and how a person's identity is checked | Today anyone can register as a patient of a customer that allows it. No identity proofing or record matching exists. | Self-registration is off by default per customer. |
| 11b | **Messages between patients and clinics** (DEV-052): are free-text messages allowed, what must a clinic do when a message describes a medical problem, how long messages are kept, whether doctors may message patients, whether photos or documents may be attached | A message can hold health information and may be read as a request for advice. | Patients and clinic staff exchange short encrypted messages; the page says it is not for emergencies; doctors cannot message yet. |

## 3. Security and engineering (CTO, Security)

| # | Decision | Why it matters | What we did meanwhile |
|---|---|---|---|
| 12 | **Cloud provider, sign-in vendor, message broker** (DEV-016) | Production cannot be set up without them. | Everything runs in Docker on one computer. |
| 13 | **Where the encryption master key lives, and what happens if it is lost** | Today it is an environment variable. A lost key means the encrypted details are unreadable forever. | Fine for development. **Not a production claim.** |
| 14 | **One incident-severity scale** (DEV-014): the documents use three different ones | Needed before any on-call or incident process. | None built. |
| 15 | **Things that must never ship** | The developer console, the unsigned test sign-in token, and the short demo passwords are for development only. A release check should refuse them. | They are switched off by default. The app already refuses to start on a database login that skips customer separation (DEV-083). The release check for the development-only settings is built (DEV-088): production refuses to start while any remains, and `check --deploy` lists them. What stays open is the key-management decision (13) and the real mail provider. |
| 16 | **Missing status lists** (DEV-015): qualification status, claim verification status, partner operations, webhooks | The documents never list them. | Not built. |

## 4. Clinical safety (Clinical Safety Officer)

| # | Decision | Why it matters | What we did meanwhile |
|---|---|---|---|
| 17 | **Referral states**: the documents list ten, we built four (sent, accepted, needs more, declined) | A real referral process needs more control. | Four states. A referral to another customer or an outside partner is not possible. |
| 18 | **Booking states and who may confirm** | The documents list sixteen booking states; we built a simple set. | Requested, confirmed, cancelled, plus attendance recorded by clinic staff. |
| 19 | **Whether Zoiko is a regulated medical product** and what clinical safety case is needed | Decides the review and testing burden. | Nothing in the product gives clinical advice or decisions. |
| 24 | **Emergency wording** on the patient Help & Support page, and the emergency number to show per country | Patients may read it in a real emergency. | A generic sentence we wrote ourselves: "not an emergency service, call your local emergency number". No number is shown. |
| 25 | **Who verifies a doctor's name, title and registration** before patients are shown it | A self-typed name can say "Dr." for someone who is not a doctor (DEV-066). | Names are typed by the person, encrypted, and marked unverified in the app. No title or registration is shown. |

## 5. Product

| # | Decision | What we did meanwhile |
|---|---|---|
| 20 | **Google sign-in**: the business must create a Google sign-in client and hand over its id | The button is shown as "not set up yet". |
| 21 | **Mail provider** for real email | A development inbox catches every email. |
| 22 | **Waiting-list and reminder rules** (priority for urgent or referred patients, how early a reminder goes) | First come first served; reminders 24 hours before. |
| 23 | **Response target and who is told when work runs late** (how many hours is acceptable, per clinic or per service; who receives the daily email) | One target of 24 hours for everyone; the daily email goes to clinic operations staff (R04) with a verified address. |
| 26 | **Who may approve suspending or reinstating a customer** (today: a second support person; should the safety officer or a manager decide instead, and may two people at one company both sign?) | A request by one support person must be approved by a different support person. The safety officer can look but not decide. |

---

## What has NOT been checked by anyone

- A security review or penetration test.
- A privacy review (data protection impact assessment).
- A clinical-safety review.
- Real users trying the screens, accessibility checking, or translation.
- Performance with real numbers of patients.
- Several details in the technical documents were read by automated extraction and not re-verified line by line.

## What we suggest you decide first

1. Items **2** and **4** (Zoiko's legal role, and clinical records). They decide whether a large part of the remaining product is allowed to exist.
2. Item **13** (key management) and item **12** (cloud and sign-in vendor). Nothing can be put in front of real patients without them.
3. Item **3** (insurers), if payment and claims are part of the first release.
