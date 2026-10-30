# 04. Tables: payer/finance, communications, partner API & connectors

All CELL tables unless marked. Money is `bigint amount_minor` + `char(3) currency` (ISO 4217) + `scale`; never summed across currencies. No card data (PAN) is ever stored; only opaque PSP tokens. Clinical payloads never enter the finance ledger. [GHE-17, GHE-13, GHE-16]

---
## A. PAYER, ENTITLEMENTS, BILLING, MARKETPLACE (schema `finance`, D12/D13) [17]

Eligibility, authorization, claim adjudication, payment are INDEPENDENT states. Coverage decision ≠ clinical decision. Payer remains authoritative.

### finance.finance_pack [P3]
Std-C. Country/subnational finance rules: `contracting_entity`, `seller_of_record`, `merchant_of_record`, `collecting_party`, `beneficial_recipient`, `settlement_route`, `principal_agent_view`, `tax_posture`/`registration`, `payer_type`, `psp_arrangement`, `permitted_payout_countries`, `refund_chargeback_owner`, `allowed_currencies[]`, `payment_rails`, `auth_mandates`, `permitted_data_fields`, `invoice_numbering_rules`, `fx_rules`, `ledger_mapping`, `referral_fee_status` (default blocked), `signed_hashes`, `activated_connectors[]`. Immutable once a transaction commits; maker-checker (Legal, Finance, Security, Privacy, Product, Clinical Safety); deny on absence/conflict.
### finance.contracting_entity [P3] INFERRED
Per capability + country; no global merchant.
### finance.commercial_agreement [P3]
`contract_ref`, `contracting_entity_ref`, `country_pack`, `counterparty_org_id`, `valid_from`, `valid_to`, `privacy_scope`, `approved_version`, role matrix (buyer, beneficiary, seller, service provider; program, expected payor, care operator, PSP), role-verification record.
### finance.fee_schedule [P3]
`price_book_id`, `payer_type`, `service_code`, `currency`, `unit`, `valid_from`, `valid_to`, `tax_class`, `discount_authority`, `version`, `decision_id`, `approval_history`. Overlapping rules denied; no retroactive repricing.
### finance.coverage_policy [P3]
`person_subject_id`, `payer_org_id`, `plan_id`, `member_ref` (a local source id, tokenized), `coverage_order` (coordination of benefits), `valid_from`, `valid_to`, `disputed` flag. Multiple policies per person; conflicts marked disputed, never auto-merged.
### finance.eligibility_check [P3]
`ref_no`, `check_ref`, `person_subject_id`, `payer_ref`, `service_ref`, `service_date` (eligibility per service date; not reusable for another date), `purpose` (payment_coverage), `consent_or_legal_basis_ref`, `policy_ref`, `raw_response_evidence_ref` + normalized view, `profile_version`, `expires_at`, `status` (REQUESTED, PENDING, VERIFIED_IN_FORCE, NOT_IN_FORCE, UNKNOWN, ERROR, EXPIRED).
### finance.benefit_status [P3]
`eligibility_check_id`, `status` (BENEFIT_CONFIRMED, LIMITED, UNKNOWN, EXHAUSTED), `source_payer`, `source_date`, plan detail (copay, deductible, co-insurance, annual cap, exclusions, out-of-network, retro-cancellation, partial approvals).
### finance.authorization_request [P3]
`ref_no` (AUT), `request_ref`, `payer_ack`, `status` (NOT_REQUIRED, REQUIRED, PENDING, APPROVED, DENIED, CANCELLED, EXPIRED), `pending_docs`, `owner`, `deadline_at`.
### finance.claim [P3]
`ref_no` (CLM), `service_coding`, `evidence_docs` (minimum necessary), `covered_person_id`, `payer_reference`, `payer_code`, `status` (DRAFT, SUBMITTED, ACCEPTED_FOR_PROCESSING, ADJUDICATED, DENIED, PENDED), `state_version`. FHIR Claim; X12 837. Out-of-order responses append evidence with a version check.
### finance.claim_response (EOB) [P3]
Versioned, payer-sourced: approved/patient shares allocated, unadjudicated amount retained, updated/pending flags, retroactive corrections versioned. FHIR ClaimResponse/ExplanationOfBenefit.
### finance.remittance [P3]
`status` (EXPECTED, REMITTANCE_RECEIVED, POSTED, RECONCILED, EXCEPTION). X12 835. Remittance ≠ cash settlement.
### finance.payer_adapter [P3]
`profile`, `version`, `country`, `topology_checksum`, `method_lane` (FHIR, EDI, manual). Source payload preserved in regional store.
### finance.entitlement_grant [P3]
`subject/program_ref`, `benefit_code`, `remaining_units`/`remaining_amount_minor`, `valid_from`, `valid_to`, `sponsor_cap`, `concurrency_token`, `state` (PROPOSED, ACTIVE, RESERVED, CONSUMED, RELEASED, EXPIRED, REVOKED). Manual override needs an adjustment event.
### finance.entitlement_reservation [P3]
Atomic reserve/commit/release with expiring `lease_expires_at`, `reservation_version`.
### finance.utilization_counter [P3]
Business key: contract + benefit + period + approved beneficiary. Serializable reserve/commit/release.
### finance.price_quote [P3]
`ref_no` (QTE), `quote_ref`, `line_versions`, `payer_share`, `patient_share`, `sponsor_share` (all minor units), `estimates`, `expires_at`, `assumptions`, `fx_basis`, `disclosure`, `seller_identity`, `quote_hash`, `accepting_actor_id`, `state` (QUOTE_DRAFT, PRICED, DISPLAYED, ACCEPTED, EXPIRED, NEW_QUOTE_VERSION). Expiry on coverage/fee/tax/seller change.
### finance.service_order [P3]
Financial order only (clinical order is in workflow/exchange). `quote_id`, `state` (ORDER_PENDING_PARTNER, ORDER_CONFIRMED, REJECTED).
### finance.payment [P3]
`ref_no` (PAY), `payment_ref`, `quote_ref`, `customer_ref`, `psp_route_ref`, `psp_client_token_ref`, `amount_minor`, `currency`, `merchant_ref`, `psp_intent_ref`, `idempotency_key`, `redirect_state`, `receipt_hash`, `status` (CREATED, AUTHENTICATION_REQUIRED, AUTHORIZED, CAPTURE_REQUESTED, CAPTURED, FAILED, UNKNOWN, SETTLED, PARTIALLY_REFUNDED, REFUNDED, DISPUTED). Server recomputes amounts; UNKNOWN triggers PSP status inquiry, never a second charge.
### finance.refund [P3]
`ref_no` (RFD), `source_capture_ref`, `amount_minor`, `terms`, `payer_split`, `psp_refund_ref`, `original_funding`, `approvals` (two-person above a threshold), `reversal_journal_ref`. Cannot exceed captured available amount; retry reuses original refund ref.
### finance.payment_dispute [P3]
`reason`, `evidence`, `due_at`, `psp_state`, `linked_refund_id`. Linked to payment so no double reversal.
### finance.marketplace_seller [P3]
`verified_legal_identity`, `kyc_kyb_result`, `sanctions_fraud_checks`, `psp_account_token`, `jurisdiction`, `fee_rate`, `local_fee_restrictions`, `seller_agreement_ref`.
### finance.payout [P3]
`psp_payout_token`, `completion_event_ref`, `maker_checker_approvals`, `destination`, `hold_reason`, `settled_state`, `status` (NOT_ELIGIBLE, ELIGIBLE, APPROVED, INSTRUCTED, PSP_ACCEPTED, PAID, RETURNED, HELD, EXCEPTION). Destination setter ≠ payout approver; mixed seller routes split.
### finance.fee_rule (commission / referral fee) [P3] INFERRED
Per jurisdiction. Referral fees, fee-splitting, inducements blocked by default; legal clearance + policy version required; clinical outcome never an input.
### finance.invoice + finance.invoice_line [P3]
`ref_no` (INV), `invoice_number` (per country pack; assigned only after entity validation), `issuer_entity`, `recipient`, `currency`, `tax_lines`/`tax_regime`, `status`, `issued_at`, `applied_payments`, `adjustments`, `accounting_export_ref`, `reversal_refs`. Issued invoice is immutable; corrections via credit note + rebill. Employer sponsor invoice: aggregated seats/units + period + totals, no clinical data.
### finance.credit_note [P3]
Immutable reversal referencing the invoice, reason, approval.
### finance.fx_record [P3]
`original_currency`, `settlement_currency`, `fx_rate`, `source`, `rate_at`, `rounding`.
### finance.ledger_journal + finance.ledger_entry [P3]
Double-entry, balanced, one currency per posting group. Accounts: receivable, payable, fees, tax, PSP-clearing. Append-only; UQ source event/transaction key = exactly one accounting effect; corrections = reversing + replacement entries with reason and approval; hash chain; principal/agent attribution (gross/net).
### finance.reconciliation_run + finance.match_group [P3]
Three-way match (invoice/order, PSP/payer, provider statement). `run_id`, `source_hashes`, `differences` (fee/tax/FX/refund), `tolerance`, `aging`, `owner`. Exceptions: orphan settlement, partial payment, unmatched. "Settled" never set from a Zoiko-initiated payment alone.
### finance.financial_event [P3]
Envelope: `event_id`, `event_type` (e.g. `PaymentSettlementObserved.v1`), `region`, `tenant_ref`, `aggregate_ref`, `source`, `occurred_at`, `received_at`, `policy_version`, `source_event_ref`, `evidence_ref`, `schema_version`, dedupe, sequence no., PSP webhook quarantine.
### finance.fraud_flag [P3]
Refund abuse, unusual PSP traffic, partner risk; pattern ids; action evidence; hold/release (dual control).
### finance.finance_role_grant / finance.approval_matrix [P3]
Roles: writer, approver, reconciler, exporter; thresholds + step-up; delegation; versioned approvals; denial codes.

---
## B. SECURE COMMUNICATIONS (schema `comms`, D16) [16]

A message sent ≠ delivered ≠ viewed ≠ acknowledged ≠ clinically acted on. Channel callbacks NEVER create clinical acknowledgment. Critical escalations use provider-owned emergency protocols, not standard push/email.

### comms.communication_intent [P2]
Std-A. `ref_no` (MSG), `intent_id`, `subject_ref`, `source_event_ref`, `workflow_ref`, `purpose_code`, `class` (C0..C4; mapped per jurisdiction ADR-16-05), `locale`, `country_pack_version`, `clinical_policy_ref`, `actor_ref`, `recipient_binding_ref`, `template_id`/`template_version`, `expires_at`, `idempotency_key` (same key different payload = 409), `policy_decision_ref`, `policy_result` (ALLOW, ALLOW_WITH_SAFE_TEMPLATE, HOLD_FOR_REVIEW, DENY), `state` (DRAFT, POLICY_PENDING, AUTHORIZED, QUEUED, SUBMITTED, ACCEPTED_BY_CHANNEL, DELIVERED_EVIDENCED, FAILED/UNKNOWN → RETRY, DENIED → BLOCKED, CANCELLED, EXPIRED), `evidence_ref`. Intent + outbox atomic.
### comms.thread [P2]
`thread_id`, patient-relationship binding, `workflow_ref`, `thread_version` (precondition on send). Never globally searchable.
### comms.thread_participant [P2] INFERRED
`thread_id`, `actor_id`, `role`, `scope`, `joined_at`, `left_at`.
### comms.message [P2]
`message_id`, `intent_id`, `thread_id`, `encrypted_content_ref` or protected deep link, `content_classification`, `creator`, `created_at`, `version`, `attachment_refs`, `signature_hash`, `retention_class`, `cancellation_ref`, `correction_ref`. Secure-access state in a separate namespace (SENT, VIEWED_AUTHENTICATED).
### comms.attachment [P2] INFERRED
`ref`, `malware_scan_result`, `classification`; regional encrypted store.
### comms.delivery_attempt [P2]
Std-B (immutable). `attempt_id`, `intent_id`, `vendor/channel`, `endpoint_token_ref`, `attempt_no`, `attempted_at`, `provider_correlation_ref`, `sanitized_error`, `retry_policy`.
### comms.delivery_receipt [P2]
Std-B. `receipt_id`, `attempt_id`, `kind` (accepted, delivered, bounced, opened), `source`, `captured_at`, `verification`, `confidence`. Late/out-of-order receipts append only and never regress state; missing receipt = UNKNOWN.
### comms.recipient_attestation [P2]
Written only via the workflow command. `actor_id`, `session`, `role`, `action` (accepted, reviewed, declined), `source_work_item_id`, `policy`, `evidence_signature`, `material_version`, `attested_at`, `source_evidence_hash`.
### comms.preference_record [P2]
`recipient`, `channels[]`, `language/locale`, `accessibility_options`, `quiet_hours` + zone, `scope` (marketing vs service), `effective_at`, `legal_restriction_overrides`, `revision`.
### comms.verified_endpoint [P2] INFERRED
Tokenized destination, `type` (email, SMS, push, voice), `verified_at`, `invalidated_at` (on change, complaint, recycling suspicion, takeover, bounce), `unverified` flag. Unverified endpoints unusable.
### comms.template_definition [P2]
Std-C. `template_id`, `locale`, `country_pack`, `audience`, `class`, `variable_whitelist`, `phi_allowed_per_channel`, `safety_owner_approval`, privacy/clinical/local release approvals, language QA signatures, `version_hash`, `sunset_at`, translations.
### comms.country_channel_policy [P2]
`country`, `policy_version`, `service`, `message_classes`, `channel_matrix` (allow/deny/hold), `transfer_basis`, `legal_basis`; release states draft → dry run → sign-off → deploy/rollback; signed manifest from the pack.
### comms.channel_vendor [P2]
`vendor`, `region`, `dpa/subprocessor_ref`, `credential_expires_at`, `circuit_breaker_state`, `backlog`, `error_rate`, `latency`, `egress_allowlist`.
### comms.secure_link [P2] INFERRED
Opaque, short-lived, scoped; revoked on cancel/redact; no PHI or bearer secret.
### comms.privacy_incident_case [P3]
Masked endpoint, event timeline, exposure suspected, actions (quarantine, revoke link, notify DPO/partner).
### comms.consent_privacy_request [P3]
Type (correction, withdrawal, erasure, data-rights, communications copy), status, requester, legal restrictions, hold flag. (Links to `policy.rights_case`.)
### comms.communication_evidence_timeline [P2]
Append-only hash-linked chain: intent, policy, attempts, receipts, secure views. Separate namespaces for provider accepted / clinical reviewed / closed.
### comms.employer_notice [P3] INFERRED
Aggregate counts only, small-cohort suppression, billing refs. No individual health content.
Events: CommunicationIntentCreated, DispatchAttempted, DeliveryReceiptObserved, SecureViewObserved, EscalationDue, ClinicalHandoffEvidenceReceived. FHIR: CommunicationRequest/Communication.

---
## C. PARTNER APIs, WEBHOOKS, CONNECTORS (schema `integration`) [13, 07, 22]

Holding an API key never grants live record access. A scope alone never authorizes protected-object access (per-call: resource, purpose, relationship, cell, feature). 202 = Zoiko accepted the request, NOT that the partner accepted it.

### integration.partner [P2]
Std-A. `partner_id`, `ref_no`, `legal_org`, `technical_contacts`, `security_contacts`, `contract_version`/`contract_ref`, `region`, `capability_scope`, `status` (ENABLED SANDBOX, ENABLED PRODUCTION, SUSPENDED, CERTIFICATION=REVOKED). Prod credentials minted only inside the regional boundary after certification.
### integration.sandbox_tenant [P2]
Per partner; synthetic data only; status; registered apps; scopes; key status.
### integration.api_client [P2]
`client_id`, `partner_id`, `region`, `client_type` (interactive OIDC+PKCE; server-to-server client-credentials + mTLS/private_key_jwt; EHR SMART), `redirect_uris` (exact match), `allowed_audience`, `rate_budget`, `expires_at`, `cert_key_thumbprint`, `auth_method`, `status` (active, suspended, revoked).
### integration.api_scope + integration.api_client_scope [P2]
`scope_code` (e.g. `operations.write`), junction `client_id`, `scope_id`.
### integration.signing_key [P2]
`key_id`, `algorithm`, `secret_version`, `rotation_log`, `overlap_window`, `expires_at`, `revoked_at`, `rollback_plan`, scope (per endpoint or asymmetric). Staged rotation key N+1 → validate → retire N.
### integration.partner_operation [P2]
`operation_ref` (opaque), `partner_id`, `purpose`, `local_order_ref`, `idempotency_key` (16-128 chars) + `payload_hash`, `correlation_ref`, `source_acceptance_proof`, `source_version`, `owner`, `status` (PENDING, SOURCE_ACCEPTANCE_PENDING, RECONCILING, UNKNOWN, ORDER_ACCEPTED, OUTPUT_AVAILABLE, declined; full list not given). UQ(partner/client, idempotency_key). Same key + different body = 409, never overwrites. Created with outbox row in one txn.
### integration.webhook_subscription [P2]
`subscription_id`, `partner_id`, `topics[]` (allowlisted), `approved_purpose`, `endpoint_url` (https only, ownership verified, no redirects, no private/reserved IPs), `allowed_path_method`, `secret_key_version` (masked), `delivery_windows`, `recipient_owner` (named), `owner_contacts`, audit (approver, time, pack, last failure), `status` (PAUSED, active, revoked; full list not given). Activation needs domain ownership + recipient verification + country/feature gates.
### integration.webhook_delivery [P2]
Std-B attempt ledger. `delivery_id`, `event_id`, `subscription_id`, `attempt_no`, timestamps, `http_response`, `retry_schedule` (exponential + jitter), `max_age`, `payload_hash`, `signature_key_id`, `safe_payload_ref`, `masked_error`, `status` (DELIVERED_TECHNICAL, RETRY, DEAD_LETTER, uncertain/timeout, cancelled, revoked). Redrive keeps same delivery/event id and payload hash; operator-only.
### integration.dead_letter_entry [P2]
`owner`, `policy`, `evidence_ref`, `age`, `first_failure_reason`, `redrive_rate`, `manual_owner`, `backlog`. Actions: diagnose, safe redrive, cancel, revoke. Regional, never deleted.
### integration.partner_connector [P2]
`connector_id`, `partner_id`, `network_id`, `endpoint_id`, `protocol` (FHIR R4/R4B/R5, SMART, HL7 v2, DICOMweb, payer EDI, flat file/SFTP), `version`, `ig_url_version`, `extension_policy`, `terminology_releases`, `field_mappings`, `allowed_lossy_fields`, `auth`, `cert_expires_at`, `jurisdiction_scope[]`, `capability_scope[]`, `allowed_operations[]`, `purpose_codes[]`, `approved_cell`, `legal_role_ref`, `contract_ref`, `cert_bundle_hash`, `adapter_digest`, `mapping_bundle_hash`, `credential_ref`, `trusted_endpoint_ref`, `emergency_stop`, `expires_at`, `health` (healthy, DEGRADED, OFFLINE), `dlq_count`, `oldest_message_age`, `freshness_map`, `state` (PROPOSED, DUE_DILIGENCE, CONTRACTED, SANDBOX, CERTIFICATION, G3_READY, G4_AUTHORIZED, CANARY, ACTIVE, DEGRADED, QUARANTINED, SUSPENDED, RETIRED, BLOCKED).
### integration.connector_certification [P2]
Certification attaches to the TUPLE (partner, market, region, capability, profile, scope, version), never a vendor globally. Lifecycle DISCOVER, DUE DILIGENCE, PROFILE PINNED, DESIGN, SANDBOX ENABLED, CONFORMANCE SUITE, SAFETY/DPIA, sign-offs (clinical, security, legal, partner), CERTIFIED, LIMITED LIVE, then MONITOR/RECERTIFY/SUSPEND/DECOMMISSION/REVOKED. Gates C0 legal, C1 connection security, C2 profile conformity, C3 functional safety, C4 resilience, C5 privacy/country, C6 operator readiness, C7 production enablement. Scored checklist per gate, `blocking_failures`, `result_digest`, signed record, `scope_limits`. C7 disabled while blockers exist; material change = recertify.
### integration.connector_permit [P2]
Scope-limited, revocable. Key: partner_id + network_id + legal_entity + jurisdiction + capability + cell_id + connector_digest + contract_ref + permitted_purposes + expiry. Checked on EVERY operation. Events PermitRevoked, ConnectorCertified.
### integration.conformance_run + integration.test_fixture [P2]
`scenario` (DUPLICATE WEBHOOK v1, INVALID SIGNATURE, ...), `source_profile`, `region`, `expected_http`, `expected_domain_effect`, `result`, `masked_request_trace`, `evidence_ref`, `test_digest`. Fixtures synthetic only. Simulator modes: 2xx/4xx/5xx, delayed, duplicate, out-of-order, bad signature, partial payload, patient mismatch, DST, no response.
### integration.partner_capability [P2]
Per market/tenant: certified vs live, declared freshness, field-level freshness map; distinguishes schedule authority, booking finality, order accepted, results released.
### integration.reconciliation_item [P2]
`operation_ref`, `current_state` (e.g. UNKNOWN), `source_version` (may be missing), `age`, `technical_receipt` (y/n), `business_acceptance_verified` (y/n), `assigned_owner`, attached partner evidence (issuer, version, scope, time). Actions: query source, attach evidence, mark conflict, escalate, approved replay, export audit.
### integration.usage_quota [P2]
Per partner/tenant/endpoint/region/risk tier; idempotency retention; rate budget; throughput. No PHI.
### integration.network_eligibility_decision / due_diligence_gate / agreement_duty_allocation [P3] [22]
`eligibility_decision_ref`, counsel sign; gate record (legal rights, contracting role, technical, clinical/safety, security, operational, signers; expired signature blocks writes); `contract_ref`, RACI hash, controller map, on-call.
### integration.mapping_registry [P2] [22]
`native_field → canonical_field → source → validation_rule → mapping_version → round_trip_fixture → known_loss`.
### integration.capability_snapshot [P2]
FHIR CapabilityStatement / HL7 v2 conformance record / DICOM mapping report.
### integration.credential_register + secret_issuance_log [P2]
`key_ref`, `auth_mode`, `token_audience`, `rotation`, `revocation`; issuance log append-only.
### integration.connector_change / canary / drift [P3] [22]
`change_impact_ref`, `canary_snapshot` + rollback trigger, `typed_receipt_evidence`, `handoff_exception_case`, `replay_authorization` (replay key, payload hash), `reconciliation_report` (requested/ack-tech/accepted-clinical/complete), `incident_evidence_chain`, `retirement_certificate`, `annex_manifest_hash`. Events: PartnerTechnicalReceipt, PartnerClinicalAcceptance, PartnerRecordCorrected, ConnectorQuarantined, ConnectorDriftDetected.
Runtime error classes: AUTH_SCOPE/LEGAL_DENY, IDENTITY_AMBIGUOUS, SCHEMA_OR_TERMINOLOGY, SOURCE_CONFLICT, RETRYABLE_NETWORK, DUPLICATE/OUT_OF_ORDER, PARTNER_DOWN/CLINICAL_URGENT, CREDENTIAL_COMPROMISE, PARTNER_VERSION_DRIFT, CROSS_CELL_REQUEST.
