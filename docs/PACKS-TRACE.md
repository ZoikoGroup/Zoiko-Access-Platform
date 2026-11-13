# Country packs: the specification's 36 acceptance cases, checked against what is built

Written 2026-11-12 by the developer, from the specification's own acceptance cases. Each row is a judgement made by reading the case and the code, not an automatic result; "Met" means one of our own automated tests proves the behaviour. The cases have **not** been run as written. This is the developer's reading and nobody else has reviewed it.

**Totals: 6 met, 17 partly, 13 not built (of 36).**

What exists is a recorded, reviewed and approved-on-record country pack with an expiry, a fingerprint and a veto, and one applied setting (the data-request deadline). The "not built" rows are mostly the signed manifest, cell staging, preflight, a run-time resolver and the transfer and market controls, none of which can be built without cells, signing keys and approvers.

| Case | Group | What the specification tests | Status | Why, and where it is tested |
|---|---|---|---|---|
| AT-01 | Verification | Unknown country/service returns DENY, not default UK/US allow. | Partly | An unknown country gives no deadline and never an allow (test_country_deadline.py). Nothing else consults packs, so there is no resolver to deny anything. |
| AT-02 | Verification | Browser locale, VPN geoIP and client-submitted country never override verified contract/site/cell. | Met | A customer's country is set only by Zoiko support; a locale, address or country sent by the patient never changes the deadline (test_country_deadline.py). |
| AT-03 | Verification | Country and subnational conflicts resolve to the stricter eligible authorization. | Partly | For the deadline, the strictest signed pack wins (test_country_deadline.py). Subnational rules are not modelled. |
| AT-04 | Verification | Missing licensing/legal entity blocks regulated service regardless of approved billing. | Not built | No licensing or legal-entity check exists; the pack only records an evidence status. |
| AT-05 | Verification | Provider license expired or scoped to wrong subregion blocks clinical handoff. | Not built | Provider licences are not modelled. |
| AT-06 | Verification | Unapproved clinical AI remains disabled despite underlying model endpoint being deployed. | Partly | A pack cannot switch on clinical AI, and AI is built switched off (test_packs.py, test_ai.py). There is no deployed model endpoint to test against. |
| AT-07 | Verification | Employer payment alone cannot enable clinician or employee-record viewing. | Met | test_prohibitions.py: the employer role is barred from patient records whatever else is true; there is no employer payment to test. |
| AT-08 | Verification | Unsigned/tampered manifest rejected by cell. | Partly | A pack's content has a fingerprint and approvals bind it (test_packs.py). There are no cells and no signatures, so nothing is rejected by a cell. |
| AT-09 | Verification | Manifest with expired dates or wrong cell mapping rejected. | Partly | An expired pack gives nothing (test_packs.py, test_country_deadline.py). There is no cell mapping. |
| AT-10 | Verification | Forged or replayed approval signature cannot activate a revision. | Not built | Approvals are rows in our own database, not signatures, so there is nothing to forge or replay. |
| AT-11 | Verification | Unknown fields and incompatible schema versions are rejected. | Partly | Unknown fields are refused (test_packs.py). Schema versions are not modelled. |
| AT-12 | Verification | Author cannot self-approve mandatory Legal/DPO/CSO/CISO approvals. | Met | test_packs.py: the author cannot approve their own pack, and two different people must approve (support and safety officers stand in for the named roles). |
| AT-13 | Verification | A material change invalidates affected signatures and produces a new immutable revision. | Met | test_packs.py: sending a pack back clears its approvals, only a draft can change, and a suspended pack returns only as a new revision. |
| AT-14 | Verification | Preflight executes without mutating live production state. | Not built | There is no preflight. |
| AT-15 | Verification | G3 production enable cannot occur with any required gate PENDING/UNKNOWN. | Partly | test_readiness.py: a release stays 'not ready' while any requirement is missing. This records a decision and blocks nothing. |
| AT-16 | Verification | Concurrent activations and conflicting revisions cannot both become effective. | Not built | There is no activation, so no concurrent activation. |
| AT-17 | Verification | Revocation reaches policy cache within verified bound; stale cache cannot newly authorize. | Partly | A suspended pack stops giving new deadlines at once (test_country_deadline.py). There is no cache to bound. |
| AT-18 | Verification | Loss of global control plane does not create new entitlements. | Not built | There is no global control plane separate from the cell. |
| AT-19 | Verification | Rollback cannot restore a now-prohibited configuration. | Partly | A suspended pack comes back only as a new draft revision, never by rolling back (test_packs.py). |
| AT-20 | Verification | In-flight clinical cases are preserved with assigned local continuation on suspension. | Partly | A deadline already given to a request stands when its pack is suspended (test_country_deadline.py). No clinical cases are involved. |
| AT-21 | Verification | Transport delivery never substitutes for clinical acknowledgment. | Met | test_orchestration.py: a delivery or a read receipt is refused as evidence of any clinical step. |
| AT-22 | Verification | Any unapproved cross-cell payload movement is rejected and locally audited. | Not built | There is one cell, so no cross-cell movement to reject. |
| AT-23 | Verification | Source and receiving jurisdictions each independently approve authorized transfers. | Not built | No cross-jurisdiction transfers. |
| AT-24 | Verification | Global registry and notifications never contain PHI, person IDs or small-cohort leakage. | Partly | Packs hold settings only, and webhook and partner messages carry references only (test_webhooks.py, test_partner_events.py). There is no global registry or notification service. |
| AT-25 | Verification | Unauthorized support impersonation and global superuser access rejected. | Partly | Support is barred from patient data and messages (test_prohibitions.py). Impersonation is not a feature, so it is not tested. |
| AT-26 | Verification | Restricted country pack cannot access connector in another market. | Not built | No markets or connectors to separate. |
| AT-27 | Verification | Missing partner contract or disabled gateway blocks service with correct user notice. | Not built | No partner contract model. |
| AT-28 | Verification | Expired evidence triggers alert ahead of deadline and suspension at required point. | Partly | An expired pack shows as expired and stops counting (test_packs.py). Nothing alerts ahead of the deadline. |
| AT-29 | Verification | Locally authorized emergency disable succeeds without unsafe clinical case loss. | Partly | Either a support or a safety officer may suspend a pack with a reason (test_packs.py). No clinical cases are involved. |
| AT-30 | Verification | Provider/tenant offboarding prevents new care, preserves required lawful records/rights. | Not built | No offboarding workflow. |
| AT-31 | Verification | Patient rights requests route to correct controlling entity and cell. | Partly | A data request goes to the patient's own clinic (test_rights.py). There are no other controlling entities or cells. |
| AT-32 | Verification | Regional restore replays same signed policy and cannot revert to obsolete permissions. | Not built | No regional restore. |
| AT-33 | Verification | Country support and accessibility/language obligations verified for approved journey. | Partly | The accessibility scan passes on every page of the four apps (browser robot 10). There is no language support. |
| AT-34 | Verification | Market claims in UI/marketing API cannot exceed approved capability set. | Not built | No marketing or capability-claim check. |
| AT-35 | Verification | Local operator access is confined to approved cell, scope, purpose and ticket duration. | Partly | Staff are confined to their own customer by row-level security and to their own organization by the rules (test_policy.py). There is no purpose or ticket duration. |
| AT-36 | Verification | Legal/clinical/security/privacy veto overrides executive/commercial approval. | Met | test_readiness.py: any veto blocks the decision whatever else is signed; no commercial approval exists to override it. |
