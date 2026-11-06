# Zoiko Access: progress report

Written 2026-11-06. These numbers are estimates unless a row says it was counted. Details of every
change are in `docs/decisions/REGISTER.md` (DEV-001 to DEV-097); what is left is in `docs/BACKLOG.md`.

## How far along are we?

| Measure | Figure | How it was found |
|---|---|---|
| Whole product, by effort | about 35% (range 30 to 40%) | Estimate from the backlog's own sizes. |
| Ready for real patients | about 20 to 25% | Estimate: reviews, hosting, mail provider and the lead's decisions are missing. |
| Work that needs no decision | about 100% | The backlog's "can be built now" list is finished. |
| Database tables | 42 of 244 designed (about 17%) | Counted in the running database. |

The table figure is low because most of the 244 tables belong to the items waiting on decisions
(clinical notes, billing, full consent, partner connections, AI evaluation).

## What works today

- **Patients:** find care, book, move and cancel with a reason, see the story of each appointment,
  referrals, family members, insurance (they choose whether the clinic's staff may see it), messages,
  privacy (who looked at my record, download my data).
- **Clinic staff:** confirm and track appointments, free times, work queue with history, waiting list,
  referrals, messages, team, doctors (with a "name checked" record), reports including a next-7-days
  view, compliance, settings.
- **Doctors:** a schedule of confirmed appointments, their patients, referrals, and the state of their
  own name check.
- **Zoiko platform staff:** customers, incidents, releases, settings, and suspending a customer (two
  different people must agree).
- **Safety built in:** every access decision is recorded, histories cannot be edited, each customer's
  data is walled off in the database itself, sensitive fields are encrypted, and production refuses to
  start with development settings.
- **AI:** only the safety rules exist (permits, tool whitelist, human approval, no text stored) and a
  fixed reply template. AI is switched off. There is no machine-learning model.

## How it was checked

| Check | Result |
|---|---|
| Full backend test run (2026-11-06, before the last few additions) | 1404 passed, 1 skipped, 1 failed (since fixed) |
| Newer tests run once each after that | all passed |
| Browser robots (open the real apps and click) | 13 tests, all passed on their last run |
| Style, type and import-rule checks | clean |
| Hand click-through by a person | **not done** |
| Security, privacy and clinical reviews | **not done** |

## What is waiting on the lead

See `docs/LEAD-NOTE.md`: eight decisions that unblock the most work, plus three about AI. In short:
whether clinical notes may be stored at all, Zoiko's legal role and first market, the provider choices
(sign-in, mail, hosting), retention and key rules, and who may verify doctors and approve AI.

## Temporary rules that need confirming

| Where | Temporary rule | Entry |
|---|---|---|
| AI permits | Any clinic administrator may propose, sign and authorize | DEV-094 |
| Doctor name checks | One administrator may check a doctor's name | DEV-095 |
| Insurance sharing | Staff cannot see a patient's insurance unless the patient shares it | DEV-096 |

## Next steps, in order

1. Send `docs/LEAD-NOTE.md` to the lead and get the decisions.
2. Do the hand click-through using `docs/CLICK-THROUGH.md`.
3. Arrange the security, privacy and clinical reviews.
4. Then build the items the answers unblock.
