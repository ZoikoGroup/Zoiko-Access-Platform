# Click-through checklist (about 45 minutes)

The browser robots (`frontend/e2e`) only prove that pages open without crashing. This list is for a
person to judge what a robot cannot: does it look right, read clearly, and feel safe to use?
Use the demo logins in `docs/RUNNING-LOCALLY.md`. Do it with the stack running. Write what is wrong next
to each line; a screenshot helps. Do NOT use the "E2E Clinic" (that is for the robots).

Tick each line when it looks right and the words make sense to a non-technical person.

## 1. Patient app (http://localhost:5174, Demo Clinic, `patient@gmail.com`)
- [ ] Sign in works; a wrong password gives one plain message (it must not say which part was wrong).
- [ ] Home: the cards and numbers make sense; no empty boxes or raw ids.
- [ ] Find Healthcare: search, filters and sorting give sensible results; free times show the clinic's time zone.
- [ ] Book a time: the confirmation says the clinic must confirm it; the booking appears under Appointments as "Waiting for the clinic".
- [ ] Appointments: History opens and reads like a story (no staff names); Change time and Cancel work; the cancel reason list is polite.
- [ ] Add to calendar downloads a file that opens in your calendar.
- [ ] My Referrals, Family Members, Insurance and Claims, Messages, Notifications, Privacy & Consent, My Profile, Help & Support: each opens, says what it is for, and has no dead buttons.
- [ ] Privacy: "who looked at my record" and "download my data" work and read clearly.
- [ ] Insurance: the "Who can see my insurance" tick-box is off at first; ticking it lets the clinic's staff see the insurer, plan, dates and last four digits (and nothing else); unticking hides it again. The wording is plain.
- [ ] Greyed-out "soon" pages say honestly that they are not built yet.
- [ ] Narrow window (about phone width): nothing is cut off or overlaps.

## 2. Staff console (http://localhost:5173, Demo Clinic, `clinicadmin@gmail.com`, then `reception@gmail.com`)
- [ ] Dashboard: Today card and late-work button make sense.
- [ ] Appointments: Confirm, Move, Cancel (with reason), Attended and Did not come; History reads clearly; "Confirm all waiting" asks first.
- [ ] Services and free times: add a time, add repeating times, remove one time, remove a range; booked times refuse to be removed with a clear message.
- [ ] Work Queue: open and closed items, History, nothing duplicated.
- [ ] Waiting list, Family requests, Referrals, Messages: each works end to end with a second login.
- [ ] Team, Providers, Organizations, Patients: lists load; the receptionist cannot do what the administrator can.
- [ ] Providers: an administrator can record that a doctor's name was checked and withdraw it; patients then see (or stop seeing) a Verified mark.
- [ ] Patients, Edit: insurance shows a clear notice when the patient has not shared it, and the details once they have.
- [ ] AI permits (administrator only): propose, move and retire a permit; the page says AI is switched off.
- [ ] Reports: "The next 7 days" shows booked versus usual per day and says it is a plain average; a new clinic sees the "not enough history" note.
- [ ] Reports and Compliance: numbers look believable; the report says its days are UTC days; the CSV opens in a spreadsheet.
- [ ] Settings: change something, reload, it stayed.
- [ ] Billing, Integrations and other "not built" pages say so honestly.

## 3. Doctor app (http://localhost:5176, `venkydoctor@gmail.com`)
- [ ] My schedule shows only confirmed appointments with this doctor.
- [ ] My patients and Referrals open; "Notes & results" and Messages say they are not built yet.
- [ ] My account works (change password, sign out), and "Is my name checked?" shows the right state after an administrator checks or withdraws it.

## 4. Platform app (http://localhost:5175, Zoiko Platform, `vikram` then `priya`)
- [ ] Overview, Customers, Incidents, Releases and Settings open and the numbers match what you know.
- [ ] Suspension: `vikram` asks, the request shows as waiting and he cannot approve it himself; `priya` approves; that customer's staff can no longer sign in; reinstate the same way. (Use a throw-away customer, not Demo Clinic.)
- [ ] `iyer` (safety officer) can look but not decide.
- [ ] Incident and release logs: add a line, it appears, and it cannot be edited afterwards.

## 5. Across all apps
- [ ] Keyboard only (no mouse): you can reach and use every button and form with Tab, Shift+Tab, Enter and Space, you can always see where you are (the focus outline), and nothing traps you.
- [ ] Screen reader (for example NVDA, free): each page has a sensible heading, buttons and fields are announced with clear names, and errors are read out.
- [ ] Zoom the page to 200% and make the window narrow: nothing is cut off or overlaps.
- [ ] Colour: nothing relies on colour alone (a red pill also has words), and it still reads in your computer's dark mode.
- [ ] Sign out really signs out (the back button does not show private pages).
- [ ] Open the mail inbox (http://127.0.0.1:8025): booking, confirmation and cancellation emails read clearly and contain no private health detail.
- [ ] Anything that looks like an error message to you: is it plain and kind, with no technical words?

## Send the results back
List each problem as: app, page, what you did, what you expected, what happened. The developer fixes them in
that order of severity: anything unsafe or wrong first, then confusing wording, then looks.
