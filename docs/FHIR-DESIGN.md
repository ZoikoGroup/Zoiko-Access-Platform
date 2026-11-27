# FHIR interoperability: design note for the lead to confirm

Written 2026-11-17; updated 2026-11-18. Steps 1 and 2 are built (validator, quarantine and staff triage). It turns the lead's decision (FHIR only, one pinned version, one country first) into a plan that can be built in small, safe steps. Items marked **Decide** need an answer first.

**Decided 18 Nov 2026:** the first country profile is **UK Core**, pinned to FHIR R4 (4.0.1). The
lead also chose the UK as the first market and confirmed that clinical notes stay switched off until
a clinical-safety review. Stage 1 and 2 of the plan below are built; UK Core checks are step 4: the first part (SNOMED CT codes, the Verhoeff check, no NHS number in a message) is built as DEV-319; the intake does not apply it yet.

## Scope of the first slice

Only what the exchange already carries, in the standard's shape:

| Our thing | FHIR resource | Direction |
|---|---|---|
| A lab, pharmacy or home-care request (`exchange` request) | `ServiceRequest` | out to a partner, in from a clinic's own system |
| A delivered result: rating, short summary, result reference | `DiagnosticReport` (status `final`, a conclusion text, the rating as a coded interpretation) | in from a partner |
| A claim | not in the first slice (needs `Claim` and payer profiles) | later |

Not in the first slice: `Patient` as a shared resource (a partner learns only name and birth date after the patient
agrees, DEV-300, and that stays as it is), `Encounter`, `Condition`, `MedicationRequest`. (`Observation` values were added later, in a `Bundle` with their report, for the treating doctor only: DEV-330.)

## Decisions to make

1. **Version.** Recommended: **FHIR R4 (4.0.1)**, pinned, with no content from R4B or R5.
2. **Country profile.** **Decided 18 Nov 2026:** the UK, with **UK Core**. The profile decides which identifiers are
   allowed and which code systems are required; those exact rules still need to be pinned from the published UK Core
   specification before step 4 is written.
3. **Code systems.** Recommended: accept the partner's own local codes alongside a standard one (LOINC for tests), and
   keep both. Anything unmapped is kept as it arrived and goes to a person to review (GHE-13 T-29), never guessed.
4. **Who may send.** Recommended: only a partner whose certification for this exact profile version is `live`
   (DEV-230); a new version starts again in the sandbox.

## How it would work

- **Receive.** A partner posts a FHIR bundle to one endpoint. The signature and the partner's identity are checked first
  (the existing signed-message path). The resource is validated against the pinned profile **before** anything is
  stored.
- **Quarantine.** A resource that fails its profile is stored in a quarantine table (encrypted, never read by the
  workflow engine) with the reason. It creates **no** clinical state: no request moves, no result appears, no one is
  notified. A person in the clinic's staff app sees "a message from X was held" and can discard or ask the partner to
  resend (T-25, T-60).
- **Accept.** A valid resource is mapped onto the existing exchange steps: a `DiagnosticReport` for a known request
  becomes "result available" with the summary and rating, exactly as a lab does today through the screen. The
  clinician review step is unchanged: delivery is never review.
- **Identifiers.** A FHIR logical id or a partner's identifier is stored as an opaque external reference and can never
  link two patients (T-27). We never build one of our own ids from one of theirs.
- **Send.** A clinic's request is rendered as a `ServiceRequest` carrying only what a partner may know today: the
  order number, the short description, the urgency and, once the patient has agreed, the name and birth date.
- **Corrections.** A later report that corrects an earlier one is stored as a new version that supersedes it; the
  original stays and is traceable (T-30), the same rule as notes.
- **Acknowledgement.** A transport receipt (`202 Accepted`) means "received", never "clinically accepted" (T-26).

## Steps, each shippable on its own

1. A validator for the pinned profile and the quarantine table with its staff screen (no clinical effect yet).
2. Receive `DiagnosticReport` for an existing request, mapped onto "result available".
3. Send `ServiceRequest` to a certified partner. **Built (DEV-323):** sent when the patient agrees, to the system the lab chose; references only.
4. Code mapping with the review queue for unmapped codes. **Built (DEV-319, DEV-321, DEV-322):** UK Core checks on the intake and two-person mapping; approved mappings are used by the intake (DEV-327).
5. Corrections and versions. **Built (DEV-325):** a correction is a new version; earlier versions are kept and readable by the treating doctor; the result is reviewed again; a withdrawn result is held for a person.

Each step adds tests that a bad resource creates no state, that the same resource twice has one effect, and that a
partner not certified for the profile is refused.

## What this does not decide

HL7 v2, imaging, terminology services, and any payer standard stay out until the lead asks for them.
