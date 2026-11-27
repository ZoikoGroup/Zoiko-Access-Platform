# 06. Enumerations (every value found in the docs)

Implementation hint: store as `text` + CHECK constraint (or a lookup table for ones that change per market); avoid native DB enums for values that may evolve. Values marked (INFERRED) are not enumerated in the docs.

## Classification (D0-D6) [05 §03]
D0 Public · D1 Organizational confidential · D2 Personal identifiable · D3 Sensitive clinical · D4 Financial/insurance sensitive · D5 Secrets/security · D6 Safety/legal evidence

## Workflow
**Instance lifecycle (R3.0 / GHE-12):** REQUESTED · VALIDATED · ACCEPTED · IN_PROGRESS · OUTPUT_AVAILABLE · CLINICAL_REVIEWED · HANDOFF_ACKNOWLEDGED · CLOSED · CANCELLED · BLOCKED
Allowed transitions: none→REQUESTED→VALIDATED→ACCEPTED→IN_PROGRESS→OUTPUT_AVAILABLE→CLINICAL_REVIEWED→HANDOFF_ACKNOWLEDGED→CLOSED; any active→CANCELLED; any active→BLOCKED. (CLINICAL_REVIEWED / HANDOFF_ACKNOWLEDGED conditional per definition.)
**Operational overlay:** CLEAR · AT_RISK · EXCEPTION · RECONCILING (+ UNKNOWN for partner outage, + exception overlay REVIEW_REASSESSMENT_REQUIRED)
**GHE-01 journey-level labels:** REQUESTED · VERIFIED · ROUTED · PARTNER_ACCEPTED · IN_PROGRESS · EVIDENCE_PENDING · COMPLETED · REJECTED · CANCELLED · EXPIRED · EXCEPTION · ESCALATED
**Journeys:** J1 Discover→book→attend · J2 Referral→diagnostics→follow-up · J3 Consultation→medication fulfilment · J4 Coverage→authorization→claim · J5 Discharge→home/community care · J6 Employer entitlement · J7 Chronic coordination · J8 Cross-border information request
**Exception codes:** PATIENT_LINK_AMBIGUOUS · PARTNER_NOT_ACKNOWLEDGED · RESULT_SOURCE_CONFLICT · CLINICAL_ATTESTATION_MISSING · HANDOFF_OWNER_MISSING · CROSS_CELL_DENIED · PARTNER_OUTCOME_UNKNOWN · POLICY_REVOKED · DEFINITION_UNSUPPORTED
**Pending external op state:** COMMITTED · UNKNOWN · PENDING_RECONCILIATION (+ RECONCILING · ERROR for idempotency console)
**Retry class:** TRANSIENT/IDEMPOTENT · UNKNOWN REMOTE RESULT · PERMANENT INVALID · MISMATCHED PATIENT · SOURCE CORRECTION
**Escalation state:** PLAN_ARMED · PRIMARY_PENDING · PRIMARY_ACCEPTED · BACKUP_DISPATCHED · BACKUP_ACCEPTED · SAFETY_EXCEPTION · CLINICAL_OWNER_REQUIRED · (human resolution) · CLOSED
**Escalation four facts (booleans/timestamps, not one status):** attempted · delivered · reached · resolved
**Evidence classes (workflow):** policy decision · transport receipt · partner business acceptance · clinical review attestation · handoff acknowledgment · escalation attempt/outcome · administrative closure · correction/revocation
**Ack class (events):** TRANSPORT_RECEIVED · TECHNICAL_ACCEPTED · BUSINESS_ACCEPTED · CLINICAL_ATTESTED
**Diagnostic handoff events:** ORDER_AUTHORISED · SOURCE_ACCEPTED · SOURCE_REPORTED · TRANSPORT_DELIVERED · CLINICIAN_REVIEW_ATTESTED · PATIENT_RELEASE_AUTHORIZED · PATIENT_NOTIFIED / CONFIRMED_VIEW · FOLLOW_UP_OWNER_ACCEPTED · (then) ADMINISTRATIVE_PATHWAY_COMPLETE
**Feature activation (cell):** DISABLED · CANARY · APPROVED · SUSPENDED

## Scheduling / referral
**Booking internal status (16):** DRAFT · VALIDATING · REQUEST_SUBMITTED · PENDING_SOURCE · CONFIRMED · REJECTED · OUTCOME_UNKNOWN · RECONCILING · TIMED_OUT · CHANGE_REQUESTED · SOURCE_CONFIRMED_NEW · CANCEL_REQUESTED · SOURCE_CANCEL_CONFIRMED · PARTNER_ACK_CANCELLED · CANCEL_OUTCOME_UNKNOWN · NEW_BOOKING_CONFIRMED (diagram-only: REBOOK_REQUESTED · OLD_BOOKING_CANCELLED)
**Partner capability:** DISCOVERY_ONLY · AVAILABILITY_READ · REQUEST_TO_BOOK · HOLD_SUPPORTED · COMMIT_SUPPORTED · RESCHEDULE_CANCEL · WAITLIST_SUPPORTED · ATTENDANCE_SUPPORTED
**Listing state:** DRAFT · EVIDENCE_PENDING · REVIEWED · PUBLISHED (+ UNVERIFIED · SUSPENDED · REVIEW REQUIRED)
**Referral type:** CLINICIAN_ORDER · SELF_REFERRAL
**Referral state:** ORDER ISSUED · ELIGIBILITY_PASS · SENT · TRANSPORT_ACK · NOT ACCEPTED · INTAKE_PENDING · ACCEPTED · REJECTED · NEEDS_INFO · ESCALATED; inbox views: Incoming · Needs clarification · Accepted · Declined
**Referral receiving decision:** accept · request more · decline

## Identity / policy
**Match state:** UNRESOLVED · POSSIBLE_MATCH · MATCHED_PENDING_AUTH · LINKED · CONFLICT · SPLIT_REVIEW · CLOSED_NOT_SAME (registration-time outputs MATCHED→BOUND · POSSIBLE/CONFLICT quarantined · NO_MATCH; R3.0: MATCHED / POSSIBLE_MATCH / UNRESOLVED / CONFLICT)
**Relationship grant status:** PROPOSED · PROOF_REQUIRED · APPROVAL_PENDING · ACTIVE · REJECTED · SUSPENDED · REVOKED · EXPIRED
**Relationship type:** parent · guardian · caregiver · legal agent · other. **Authority source:** self-grant · legal instrument · provider attestation
**Access decision:** PERMIT · DENY · STEP_UP · REVIEW
**Actor type:** patient · clinician/allied · facility org user · caregiver/guardian/proxy · payer/employer · service workload/partner · platform support
**Cell lifecycle:** Proposed · Privacy assessed · Approved for limited pilot · Active · Suspended · Decommissioning
**DPIA status:** DRAFT · REVIEW · BLOCKED · APPROVED
**Rights request type:** access · correction · erasure · portability · restriction · objection · consent withdrawal. **Task state:** sent · accepted · response delivered. **Notification state:** draft · authorized · delivered
**Pack lifecycle:** DRAFT · VALIDATED · EXPERT_REVIEW · SIGNED · STAGED_IN_CELL · (PREFLIGHT+CANARY) · (LOCAL G3 GO-LIVE) · ACTIVE · SUSPENDED · RETIRED (also `proposed`)
**Capability state (country/service):** UNAVAILABLE · ASSESSED · IMPLEMENTED · APPROVED · ACTIVE · SUSPENDED · RETIRED
**Market readiness:** NOT_ASSESSED · NOT_PERMITTED · VALIDATED_FOR_DESIGN · READY_FOR_CONTROLLED_PILOT · ACTIVE_WITH_CONDITIONS · SUSPENDED · RETIRED
**Gates:** G0 Strategy · G1 Design · G2 Build · G3 Pilot · G4 Scale; decision APPROVE · RETURN WITH CHANGES · REJECT
**Preflight check status:** PASS · BLOCK · PENDING · N/A-REVIEW · EVIDENCE
**Pack approval roles:** country/legal counsel · country clinical lead/CSO · DPO/privacy · CISO/security · platform CTO/engineering · country operating executive (no regulatory veto)

## Clinical exchange
**Exchange delivery state:** CREATED · VALIDATED · AUTHORIZED · QUEUED · SENT · ACKED_TECHNICAL · ACCEPTED_SOURCE · CLINICALLY_REVIEWED · FOLLOW_UP_ACK · (RECONCILE · RETRY · QUARANTINE · ESCALATE)
**API dual state:** `technical_state` (e.g. QUEUED) + `semantic_state` (e.g. UNCONFIRMED)
**Failure class:** SECURITY_DENIED · PATIENT_UNRESOLVED · STRUCTURE_INVALID · SEMANTIC_UNSAFE · DELIVERY_UNKNOWN · CORRECTION_CONFLICT · SOURCE_UNAVAILABLE · DISCLOSURE_INCIDENT
**Order/result state:** SENT/UNCONFIRMED · REJECTED (specimen) · PRELIMINARY · FINAL · AMENDED · SOURCE_FLAG_PRESERVED · CANCELLED
**Terminology mapping state:** UNMAPPED · PROPOSED · CLINICALLY_REVIEWED · VALIDATED · ACTIVE · DEPRECATED · REVOKED
**Medication entities:** MedicationRequest · MedicationDispense · MedicationAdministration · MedicationStatement
**Event types (exchange):** health.exchange.received.v1 · validation_failed.v1 · semantic_accepted.v1 · health.source.record_amended.v1 · health.result.source_status_changed.v1 · health.exchange.disclosure_denied.v1 · reconciliation_required.v1

## Commands / errors
**Error codes:** POLICY_DENIED · JURISDICTION_INACTIVE · SUBJECT_LINK_UNVERIFIED · CONFLICT_VERSION · DUPLICATE_COMMAND · UNSAFE_REPLAY · PARTNER_UNCONFIRMED · CLINICAL_ACCEPTANCE_ABSENT; HTTP: 409 version conflict · 422 semantic invalid · 403 policy denied · 423 hold; command outcomes CONFLICT · VERSION_CONFLICT · DENIED
**Outbox state:** pending · published · failed · quarantined (INFERRED)

## Partner / API
**Partner status:** ENABLED SANDBOX · ENABLED PRODUCTION · SUSPENDED · CERTIFICATION=REVOKED
**Client type:** interactive (OIDC code+PKCE) · server-to-server (client credentials + mTLS / private_key_jwt) · EHR SMART. **Client status:** active · suspended · revoked
**Partner operation status (partial):** PENDING · SOURCE_ACCEPTANCE_PENDING · RECONCILING · UNKNOWN · ORDER_ACCEPTED · OUTPUT_AVAILABLE · declined
**Webhook delivery status:** DELIVERED_TECHNICAL · RETRY · DEAD_LETTER · uncertain (timeout) · cancelled · revoked. **Subscription status (partial):** PAUSED · active · revoked
**Connector lifecycle:** PROPOSED · DUE_DILIGENCE · CONTRACTED · SANDBOX · CERTIFICATION · G3_READY · G4_AUTHORIZED · CANARY · ACTIVE · DEGRADED · QUARANTINED · SUSPENDED · RETIRED · BLOCKED. **Health:** healthy · DEGRADED · OFFLINE
**Certification lifecycle:** DISCOVER · DUE DILIGENCE · PROFILE PINNED · DESIGN · SANDBOX ENABLED · CONFORMANCE SUITE · SAFETY/DPIA · CERTIFIED · LIMITED LIVE · MONITOR · RECERTIFY · SUSPEND · DECOMMISSION · REVOKED. **Gates:** C0 legal · C1 security · C2 profile · C3 safety · C4 resilience · C5 privacy/country · C6 operator · C7 production enablement
**Per-connector decision:** BLOCK · SANDBOX ONLY · CERTIFIED · G4 AUTHORIZED · SUSPENDED
**Connector runtime error classes:** see 04 §C

## Communications
**Message class:** C0 · C1 · C2 · C3 · C4. **Policy result:** ALLOW · ALLOW_WITH_SAFE_TEMPLATE · HOLD_FOR_REVIEW · DENY
**Notification state:** DRAFT · POLICY_PENDING · AUTHORIZED · QUEUED · SUBMITTED · ACCEPTED_BY_CHANNEL · DELIVERED_EVIDENCED · FAILED/UNKNOWN→RETRY · DENIED→BLOCKED · CANCELLED · EXPIRED
**Receipt kind:** accepted · delivered · bounced · opened. **Secure-access:** SENT · VIEWED_AUTHENTICATED. **Recipient attestation action:** accepted · reviewed · declined. **Channel type:** email · SMS · push · voice

## Finance
**Eligibility status:** REQUESTED · PENDING · VERIFIED_IN_FORCE · NOT_IN_FORCE · UNKNOWN · ERROR · EXPIRED. **Benefit:** BENEFIT_CONFIRMED · LIMITED · UNKNOWN · EXHAUSTED
**Authorization:** NOT_REQUIRED · REQUIRED · PENDING · APPROVED · DENIED · CANCELLED · EXPIRED
**Claim:** DRAFT · SUBMITTED · ACCEPTED_FOR_PROCESSING · ADJUDICATED · DENIED · PENDED. **Remittance:** EXPECTED · REMITTANCE_RECEIVED · POSTED · RECONCILED · EXCEPTION
**Entitlement:** PROPOSED · ACTIVE · RESERVED · CONSUMED · RELEASED · EXPIRED · REVOKED
**Quote/order:** QUOTE_DRAFT · PRICED · DISPLAYED · ACCEPTED · EXPIRED · NEW_QUOTE_VERSION → ORDER_PENDING_PARTNER · ORDER_CONFIRMED · REJECTED
**Payment:** CREATED · AUTHENTICATION_REQUIRED · AUTHORIZED · CAPTURE_REQUESTED · CAPTURED · FAILED · UNKNOWN · SETTLED · PARTIALLY_REFUNDED · REFUNDED · DISPUTED
**Payout:** NOT_ELIGIBLE · ELIGIBLE · APPROVED · INSTRUCTED · PSP_ACCEPTED · PAID · RETURNED · HELD · EXCEPTION
**Finance roles:** writer · approver · reconciler · exporter

## Safety / security / release / quality
**Hazard status:** OPEN · VERIFIED · REJECTED · ACCEPTED · RETIRED
**Clinical safety incident state:** DETECTED · TRIAGED · CONTAINMENT_IN_PROGRESS · CONTAINED · INVESTIGATION · CORRECTIVE_ACTION · VERIFIED_RECOVERY · CLOSED/MONITORING (+ POTENTIAL_HARM · CLINICAL_PROVIDER_NOTIFIED · ESCALATE · REGULATORY_REVIEW). **Impact:** suspected · actual · unknown. **Source:** patient · provider · Zoiko · regulator · monitoring
**Security incident state:** DETECTED · TRIAGED · DECLARED · CONTAINING · LEGAL/CLINICAL + FORENSIC PRESERVE · ERADICATING · RECOVERING+VERIFY · CLOSED (REOPEN; COUNTRY FEATURE SUSPEND). **Tier:** P0 · P1 · P2 · P3
**Feature safety manifest lifecycle:** draft · assessment · blocked · approved · suspended · retired
**Threat state:** open · mitigating · test_pending · accepted · blocked · retired
**Severity (CONFLICT, needs one enum):** SEV-C, SEV-1..4 [18] · SEV-0..3 [20] · P0..P3 [09]
**Decision result (alerts/JIT/security):** permit · deny · step_up · error
**Test result:** PASS · FAIL · BLOCKED · NOT_RUN · INVALID. **Defect lifecycle:** DETECTED · TRIAGED · OWNED · FIXED · RETESTED · REVIEWED · CLOSED/REOPENED. **Release assessment:** NO_GO · CONDITIONALLY_REVIEWABLE · READY_FOR_GHE20
**Release candidate state:** DRAFT · EVIDENCE_REVIEW · G3_READY · G4_AUTHORIZED · PILOT_ACTIVE · EXPANDING · STEADY_STATE · HOLD · SUSPENDED · ROLLBACK · DEGRADED. **Gate outcome:** NO_GO · G3_READY · G4_AUTHORIZED · PILOT_ACTIVE · SUSPENDED
**Capability activation lifecycle:** PROPOSED · SCOPING · EVIDENCE_PENDING · ASSESSMENT_READY · APPROVED_FOR_BUILD · VALIDATION_PENDING · VALIDATED · AUTHORISED_FOR_PILOT · ACTIVE · ON_HOLD · SUSPENDED · REVALIDATION_PENDING · RETIRING · RETIRED
**SLO budget:** WITHIN · EXHAUSTED · UNKNOWN. **SLO source health:** GOOD · PARTIAL · UNAVAILABLE. **Degradation:** green · degraded · read-only/limited · blocked
**AI permit state:** PROPOSED · QUALIFICATION · DESIGN_CONTROL · VALIDATION · SANDBOX · SHADOW · RELEASE_REVIEW · AUTHORIZED · SUSPENDED · CHANGE_REVIEW · CORRECTIVE_ACTION · REVALIDATION · RETIRED. **AI output decision:** ALLOWED · HUMAN_REVIEW · REJECTED · QUARANTINED. **AI levels:** A0 · A1 · A2 · A3
**Analytics tiers:** T0 reliability · T1 tenant ops · T2 care-delivery quality · T3 privacy-protected population · T4 secondary research/AI (OFF). **Disclosure outcome:** RELEASE · DENY · GENERALIZE
**Cross-border analytics outcome:** none · safe aggregate · approved identifiable transfer
**Roles:** R01 Patient · R02 Guardian/caregiver · R03 Clinician/allied · R04 Clinic/hospital ops · R05 Lab/imaging operator · R06 Pharmacy/dispensing partner · R07 Payer/insurer/TPA · R08 Employer sponsor · R09 Community/home-care partner · R10 Integration partner developer · R11 Regional operations/support · R12 Clinical safety officer
**Domains:** D01 Trust/parties/tenancy · D02 Policy/purpose/rights · D03 Partner/service catalog · D04 Access/scheduling · D05 Workflow fabric · D06 Clinical exchange · D07 Referrals/care coordination · D08 Diagnostics/imaging · D09 Medication/pharmacy · D10 Consultations/virtual care · D11 Patient navigation · D12 Payer/coverage · D13 Commerce/settlement · D14 Home/community care · D15 Occupational/population programs · D16 Messaging/collaboration · D17 Analytics/evidence · D18 Governed AI/device
