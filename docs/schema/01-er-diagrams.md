# 01. ER diagrams (relationships)

Mermaid syntax (renders in VS Code with a Mermaid preview extension, or GitHub). Only keys and the important columns are shown. Full columns are in files 02-05.
Legend: `||--o{` one-to-many, `}o--o{` many-to-many (via junction table), `||--||` one-to-one. FKs inside a cell never cross to another cell. Cross-cell links are by signed permit only, never by FK.

## D1. Planes and cells (global vs cell)

```mermaid
erDiagram
    GOV_JURISDICTION_PACK ||--o{ GOV_PACK_REVISION : "has revisions"
    GOV_PACK_REVISION ||--o{ GOV_PACK_APPROVAL : "signed by"
    GOV_PACK_REVISION }o--o{ GOV_CELL : "approved for (via gov_pack_cell)"
    GOV_CELL ||--o{ GOV_CELL_MANIFEST : "versions"
    GOV_CAPABILITY_DEFINITION ||--o{ GOV_ACTIVATION_INSTANCE : "instantiated as"
    GOV_ACTIVATION_INSTANCE }o--|| GOV_PACK_REVISION : "under"
    GOV_ACTIVATION_INSTANCE }o--|| GOV_CELL : "in"
    GOV_RELEASE_CANDIDATE ||--o{ GOV_GATE_DECISION : "decided by"
    GOV_RELEASE_CANDIDATE ||--o{ GOV_COUNTRY_PERMIT : "grants"
    GOV_FEATURE_SAFETY_MANIFEST }o--|| GOV_CAPABILITY_DEFINITION : "covers"
    GOV_CONNECTOR_CATALOG ||--o{ GOV_CONNECTOR_CERTIFICATION : "certified as"
    GOV_EVENT_SCHEMA ||--o{ GOV_EVENT_SCHEMA_VERSION : "versions"
```

## D2. Identity, tenancy, delegation (cell)

```mermaid
erDiagram
    TENANT ||--o{ ORGANIZATION : "contains"
    ORGANIZATION ||--o{ FACILITY : "has"
    ORGANIZATION ||--o{ ORG_AFFILIATION : "employs"
    ACTOR ||--o{ ORG_AFFILIATION : "member of"
    ACTOR ||--o{ ROLE_ASSIGNMENT : "holds"
    ROLE ||--o{ ROLE_ASSIGNMENT : "granted as"
    ROLE ||--o{ ROLE_PERMISSION : "includes"
    PERMISSION ||--o{ ROLE_PERMISSION : "in"
    ACTOR ||--o{ PROFESSIONAL_QUALIFICATION : "has credentials"
    ACTOR ||--o| PERSON_SUBJECT : "may be (patient)"
    PERSON_SUBJECT ||--o{ SOURCE_PATIENT_LINK : "linked to source ids"
    PERSON_SUBJECT ||--o{ MATCH_CASE : "match review"
    PERSON_SUBJECT ||--o{ RELATIONSHIP_GRANT : "subject of delegation"
    ACTOR ||--o{ RELATIONSHIP_GRANT : "delegate"
    ACTOR ||--o{ RELATIONSHIP_GRANT : "granter"
    ACTOR ||--o{ CARE_RELATIONSHIP : "treating"
    PERSON_SUBJECT ||--o{ CARE_RELATIONSHIP : "treated"
    ACTOR ||--o{ SESSION : "authenticates"
    ACTOR ||--o{ PROOFING_SESSION : "identity proofing"
    WORKLOAD_IDENTITY }o--|| TENANT : "scoped to"
```

## D3. Policy, purpose, privacy

```mermaid
erDiagram
    FLOW_REGISTER ||--o{ FLOW_REVISION : "revisions"
    FLOW_REVISION ||--o{ PURPOSE_GRANT : "basis for"
    PERSON_SUBJECT ||--o{ PURPOSE_GRANT : "grants/authority"
    ACCESS_DECISION }o--|| ACTOR : "actor"
    ACCESS_DECISION }o--o| PERSON_SUBJECT : "subject"
    ACCESS_DECISION }o--o| RELATIONSHIP_GRANT : "via proxy"
    ACCESS_DECISION }o--o| CARE_RELATIONSHIP : "via care link"
    ACCESS_DECISION }o--|| FLOW_REVISION : "under flow"
    TRANSFER_REQUEST ||--o| TRANSFER_PERMIT : "if approved"
    TRANSFER_REQUEST }o--|| FLOW_REVISION : "uses"
    RIGHTS_CASE }o--|| PERSON_SUBJECT : "about"
    RIGHTS_CASE ||--o{ RIGHTS_CASE_TASK : "controller tasks"
    DPIA }o--o{ FLOW_REVISION : "assesses (dpia_flow)"
    PRIVILEGED_ACCESS_GRANT }o--|| ACTOR : "requester"
    BREAK_GLASS_SESSION }o--|| ACTOR : "clinician"
    BREAK_GLASS_SESSION }o--|| PERSON_SUBJECT : "patient"
    LEGAL_HOLD }o--o{ RETENTION_LEDGER : "suspends expiry"
```

## D4. Catalog, scheduling, referral (core of J1/J2)

```mermaid
erDiagram
    ORGANIZATION ||--o{ PRACTITIONER_ROLE : "offers"
    PRACTITIONER ||--o{ PRACTITIONER_ROLE : "works as"
    LOCATION ||--o{ PRACTITIONER_ROLE : "at"
    HEALTHCARE_SERVICE ||--o{ PRACTITIONER_ROLE : "delivers"
    ORGANIZATION ||--o{ LOCATION : "operates"
    ORGANIZATION ||--o{ HEALTHCARE_SERVICE : "provides"
    HEALTHCARE_SERVICE ||--o{ VERIFICATION_CLAIM : "evidenced by"
    PRACTITIONER ||--o{ VERIFICATION_CLAIM : "evidenced by"
    ORGANIZATION ||--o{ VERIFICATION_CLAIM : "evidenced by"
    HEALTHCARE_SERVICE ||--o| DIRECTORY_PROJECTION : "published as"
    PARTNER_ENDPOINT }o--|| ORGANIZATION : "connects"
    PERSON_SUBJECT ||--o{ BOOKING_INTENT : "requests"
    BOOKING_INTENT }o--|| HEALTHCARE_SERVICE : "for"
    BOOKING_INTENT ||--o{ AVAILABILITY_SNAPSHOT : "offers"
    BOOKING_INTENT ||--o| BOOKING_HOLD : "may hold"
    BOOKING_INTENT ||--o| PARTNER_BOOKING : "confirmed as"
    BOOKING_INTENT ||--o{ BOOKING_OPERATION : "operations"
    PARTNER_BOOKING ||--o{ BOOKING_EVENT : "history (append-only)"
    BOOKING_INTENT ||--o| WORKFLOW_INSTANCE : "driven by"
    REFERRAL ||--o| WORKFLOW_INSTANCE : "driven by"
    REFERRAL }o--|| PERSON_SUBJECT : "for patient"
    REFERRAL }o--|| REFERRAL_DESTINATION : "to"
    REFERRAL ||--o{ REFERRAL_DECISION : "receiving decision"
    REFERRAL ||--o{ REFERRAL_DOCUMENT : "attachments"
```

## D5. Workflow fabric (the spine)

```mermaid
erDiagram
    WORKFLOW_DEFINITION ||--o{ WORKFLOW_INSTANCE : "pinned version"
    WORKFLOW_INSTANCE ||--o{ WORKFLOW_TRANSITION : "append-only log"
    WORKFLOW_INSTANCE ||--o{ WORK_ITEM : "work to do"
    WORKFLOW_INSTANCE ||--o{ WORKFLOW_TIMER : "deadlines"
    WORKFLOW_INSTANCE ||--o{ EXCEPTION_CASE : "exceptions"
    WORKFLOW_INSTANCE ||--o{ PARTNER_HANDOFF : "handoffs"
    WORKFLOW_INSTANCE ||--o{ PENDING_EXTERNAL_OPERATION : "unknown outcomes"
    WORKFLOW_INSTANCE ||--o| CLOSURE_SNAPSHOT : "on close"
    WORKFLOW_INSTANCE ||--o{ ESCALATION_INSTANCE : "escalations"
    ESCALATION_PLAN ||--o{ ESCALATION_INSTANCE : "followed"
    ESCALATION_INSTANCE ||--o{ ESCALATION_ATTEMPT : "attempts"
    ONCALL_ROSTER ||--o{ ESCALATION_INSTANCE : "targets"
    WORKFLOW_TRANSITION }o--o{ EVIDENCE_RECORD : "cites (transition_evidence)"
    WORKFLOW_INSTANCE }o--|| PERSON_SUBJECT : "about"
    WORKFLOW_INSTANCE }o--|| ORGANIZATION : "owner org"
    WORK_ITEM }o--o| ACTOR : "assigned to"
    EXCEPTION_CASE }o--o| ACTOR : "owner"
```

## D6. Clinical exchange, provenance, evidence

```mermaid
erDiagram
    EXCHANGE_ENVELOPE ||--|| SOURCE_VAULT_OBJECT : "original bytes"
    EXCHANGE_ENVELOPE ||--o{ SOURCE_PROVENANCE : "provenance chain"
    EXCHANGE_ENVELOPE }o--o| EXCHANGE_ENVELOPE : "supersedes (correction)"
    EXCHANGE_ENVELOPE }o--|| PERSON_SUBJECT : "bound to"
    EXCHANGE_ENVELOPE }o--|| PARTNER_CONNECTOR : "via"
    EXCHANGE_ENVELOPE }o--o| TERMINOLOGY_MAPPING : "mapped with"
    TERMINOLOGY_MAPPING }o--|| TERMINOLOGY_RELEASE : "pinned"
    DIAGNOSTIC_ORDER ||--o{ RESULT_REF : "results"
    RESULT_REF }o--|| EXCHANGE_ENVELOPE : "source payload"
    RESULT_REF ||--o{ CLINICAL_ATTESTATION : "reviewed by"
    DIAGNOSTIC_ORDER }o--|| WORKFLOW_INSTANCE : "tracked by"
    QUARANTINE_ITEM }o--|| EXCHANGE_ENVELOPE : "held"
    CORRECTION_CASE }o--|| RESULT_REF : "v1 to v2"
    EVIDENCE_RECORD }o--o| SOURCE_VAULT_OBJECT : "points to"
    AUDIT_EVENT }o--|| ACTOR : "by"
    DISCLOSURE_LOG }o--|| ACCESS_DECISION : "per decision"
    OUTBOX_EVENT }o--|| DOMAIN_EVENT : "publishes"
    INBOX_MESSAGE }o--|| DOMAIN_EVENT : "dedupes"
```

## D7. Payer, entitlements, billing, ledger

```mermaid
erDiagram
    COMMERCIAL_AGREEMENT ||--o{ FEE_SCHEDULE : "prices"
    COMMERCIAL_AGREEMENT ||--o{ ENTITLEMENT_GRANT : "funds"
    ENTITLEMENT_GRANT ||--o{ ENTITLEMENT_RESERVATION : "leases"
    ENTITLEMENT_GRANT ||--o{ UTILIZATION_COUNTER : "counts"
    PERSON_SUBJECT ||--o{ COVERAGE_POLICY : "covered by"
    COVERAGE_POLICY ||--o{ ELIGIBILITY_CHECK : "checked"
    ELIGIBILITY_CHECK ||--o{ BENEFIT_STATUS : "returns"
    ELIGIBILITY_CHECK ||--o{ AUTHORIZATION_REQUEST : "may require"
    AUTHORIZATION_REQUEST ||--o{ CLAIM : "supports"
    CLAIM ||--o{ CLAIM_RESPONSE : "adjudicated"
    CLAIM_RESPONSE ||--o{ REMITTANCE : "paid via"
    PRICE_QUOTE ||--o| SERVICE_ORDER : "accepted as"
    SERVICE_ORDER ||--o{ PAYMENT : "paid by"
    PAYMENT ||--o{ REFUND : "refunded"
    PAYMENT ||--o{ PAYMENT_DISPUTE : "disputed"
    SERVICE_ORDER ||--o{ INVOICE : "invoiced"
    INVOICE ||--o{ INVOICE_LINE : "lines"
    INVOICE ||--o{ CREDIT_NOTE : "reversed by"
    MARKETPLACE_SELLER ||--o{ PAYOUT : "paid"
    LEDGER_JOURNAL ||--o{ LEDGER_ENTRY : "balanced lines"
    PAYMENT }o--o{ LEDGER_JOURNAL : "posts (source_event)"
    INVOICE }o--o{ LEDGER_JOURNAL : "posts"
    RECONCILIATION_RUN ||--o{ MATCH_GROUP : "three-way match"
    MATCH_GROUP }o--o{ PAYMENT : "includes"
    MATCH_GROUP }o--o{ REMITTANCE : "includes"
    MATCH_GROUP }o--o{ INVOICE : "includes"
```

## D8. Communications and escalation

```mermaid
erDiagram
    COMMUNICATION_INTENT ||--o{ MESSAGE : "creates"
    THREAD ||--o{ MESSAGE : "contains"
    THREAD ||--o{ THREAD_PARTICIPANT : "roster"
    MESSAGE ||--o{ ATTACHMENT : "files"
    COMMUNICATION_INTENT ||--o{ DELIVERY_ATTEMPT : "attempts"
    DELIVERY_ATTEMPT ||--o{ DELIVERY_RECEIPT : "receipts (append-only)"
    COMMUNICATION_INTENT ||--o{ RECIPIENT_ATTESTATION : "attested via workflow"
    TEMPLATE_DEFINITION ||--o{ COMMUNICATION_INTENT : "rendered from"
    COUNTRY_CHANNEL_POLICY ||--o{ COMMUNICATION_INTENT : "governs"
    PREFERENCE_RECORD ||--o{ VERIFIED_ENDPOINT : "owns"
    VERIFIED_ENDPOINT ||--o{ DELIVERY_ATTEMPT : "sent to"
    CHANNEL_VENDOR ||--o{ DELIVERY_ATTEMPT : "carried by"
    COMMUNICATION_INTENT }o--o| WORKFLOW_INSTANCE : "from"
    ESCALATION_INSTANCE }o--o| COMMUNICATION_INTENT : "may send"
```

## D9. Partner APIs, connectors, certification

```mermaid
erDiagram
    PARTNER ||--o{ SANDBOX_TENANT : "has"
    PARTNER ||--o{ API_CLIENT : "registers"
    API_CLIENT ||--o{ API_CLIENT_SCOPE : "scopes"
    API_CLIENT ||--o{ SIGNING_KEY : "keys (rotation)"
    PARTNER ||--o{ PARTNER_CONNECTOR : "connects via"
    PARTNER_CONNECTOR ||--o{ CONNECTOR_CERTIFICATION : "certified (tuple)"
    CONNECTOR_CERTIFICATION ||--o{ CONFORMANCE_RUN : "tested by"
    PARTNER_CONNECTOR ||--o{ CONNECTOR_PERMIT : "permit (scope-limited)"
    PARTNER ||--o{ WEBHOOK_SUBSCRIPTION : "subscribes"
    WEBHOOK_SUBSCRIPTION ||--o{ WEBHOOK_DELIVERY : "deliveries"
    WEBHOOK_DELIVERY ||--o| DEAD_LETTER_ENTRY : "if failed"
    API_CLIENT ||--o{ PARTNER_OPERATION : "invokes"
    PARTNER_OPERATION ||--o{ RECONCILIATION_ITEM : "if unknown"
    PARTNER_CONNECTOR ||--o{ PARTNER_CAPABILITY : "declares"
```

## D10. Safety, security, release, quality (governance spine)

```mermaid
erDiagram
    INTENDED_PURPOSE ||--o{ HAZARD : "hazards"
    HAZARD }o--o{ CONTROL : "mitigated by (hazard_control)"
    CONTROL }o--o{ TEST_CASE : "verified by (control_test)"
    REQUIREMENT }o--o{ HAZARD : "traces (requirement_hazard)"
    REQUIREMENT }o--o{ TEST_CASE : "covered by (requirement_test)"
    TEST_CASE ||--o{ TEST_RUN : "executed"
    TEST_RUN ||--o{ EVIDENCE_RECORD : "produces"
    TEST_RUN ||--o{ DEFECT : "may raise"
    SAFETY_CASE }o--o{ HAZARD : "rolls up"
    SAFETY_CASE ||--o{ RELEASE_ATTESTATION : "supports"
    RELEASE_ATTESTATION }o--|| RELEASE_CANDIDATE : "for"
    CLINICAL_SAFETY_INCIDENT }o--o| HAZARD : "relates"
    CLINICAL_SAFETY_INCIDENT }o--o| SECURITY_INCIDENT : "linked"
    FEATURE_SUSPENSION }o--o| CLINICAL_SAFETY_INCIDENT : "caused by"
    SECURITY_INCIDENT ||--o{ SECURITY_ALERT : "from"
    THREAT_RECORD }o--o{ CONTROL : "mitigated by"
    APPROVAL_DECISION }o--|| RELEASE_CANDIDATE : "for"
    READINESS_EXCEPTION }o--|| RELEASE_CANDIDATE : "waives"
```

## D11. Master reference chain: "one request end to end" (J1)

```mermaid
erDiagram
    ACTOR ||--o{ ACCESS_DECISION : "1 every call is decided"
    ACCESS_DECISION ||--o{ AUDIT_EVENT : "2 and audited"
    PERSON_SUBJECT ||--o{ BOOKING_INTENT : "3 patient requests"
    BOOKING_INTENT ||--|| WORKFLOW_INSTANCE : "4 workflow starts"
    WORKFLOW_INSTANCE ||--o{ WORKFLOW_TRANSITION : "5 each step logged"
    WORKFLOW_TRANSITION ||--o{ OUTBOX_EVENT : "6 same transaction"
    OUTBOX_EVENT ||--o{ PARTNER_OPERATION : "7 sent to partner"
    PARTNER_OPERATION ||--o| PARTNER_BOOKING : "8 partner confirms"
    PARTNER_BOOKING ||--o{ BOOKING_EVENT : "9 evidence"
    BOOKING_EVENT ||--o{ COMMUNICATION_INTENT : "10 notify patient"
    COMMUNICATION_INTENT ||--o{ DELIVERY_ATTEMPT : "11 delivery tracked"
```
