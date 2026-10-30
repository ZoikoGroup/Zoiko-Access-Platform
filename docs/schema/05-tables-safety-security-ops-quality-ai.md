# 05. Tables: clinical safety, security, ops/SRE, verification, go-live, activation, AI

Mostly governance records. Split by plane: records that touch a person/incident/evidence live in the CELL; manifests, catalogs and hashes live in the GLOBAL plane (`gov`). "Std-A/B/C" as in 02. The docs are explicit that these are PROPOSED; field lists are the specs' own "minimum" lists.

---
## A. CLINICAL SAFETY (schema `safety`) [08]

Safety assurance is per FEATURE, not per geography. No clinical capability without: named operator, receiving-party responsibility, hazard review, legal rights, verified partner, local authorization. (Release veto.)

### safety.intended_purpose_statement [P1 before any clinical feature] (global `gov` or cell)
Std-C. `ips_id`, `feature_key`, `version`, `function_output`, `patient_population`, `intended_user`, `clinical_context`, `decision_influence`, `exclusions`, `foreseeable_misuse`, `function_class` (A administrative/directory, B clinically consequential coordination; C-E not read), `marketing_claims_control`. Version-controlled; marketing claims checked against it.
### safety.qualification_dossier [P4]
Per feature+country: `version`, `country_pack`, `ips_id`, `owner`, `device_ivd` (OPEN), `provider_activity` (OPEN), `ai` (OPEN), `legal_opinion`, `clinical_owner`, `device_qms`, `required_standards`, `evidence_links`, `expires_at`, `status` (NOT CLEARED...). Admins can submit evidence but cannot self-approve.
### safety.hazard [P1]
Std-A. `ref_no` (HAZ; H01..H20 seeded; AI hazards HAI-01..16 separate library), `title`, `hazardous_situation`, `exposure_path`, `affected_groups`, `severity` (clinical assessor), `probability` + `basis_method`, `residual_risk`, `owner`, `review_dates`, `approvals`, `linked_ips_id`, `linked_build`, `independent_reviewer`, `status` (OPEN, VERIFIED, REJECTED, ACCEPTED, RETIRED).
Seed hazards: H01 wrong-patient result; H02 wrong order/result correlation; H03 preliminary seen as final; H04 corrected report unnoticed; H05 clinical ACK forged by delivery; H06 critical message not actioned; H07 autonomous critical prioritization; H08 referral lost/duplicated; H09 stale professional credentials; H10 unsafe patient notification; H11 unit/terminology conversion; H12 incomplete handover; H13 emergency workflow misconstrued; H14 partner outage/lag; H15 wrong or expired market pack; H16 consent withdrawn; H17 AI drives clinical action; H18 unsafe config rollout; H19 patient cannot understand instruction; H20 poor post-incident traceability.
### safety.control [P1]
`control_id` (C-###), `description`, `owner`, `approver`, `effectiveness_link` (build + partner config), `status`. Junctions `hazard_control`, `control_test`.
### safety.risk_acceptance [P4]
Named action, reasons, evidence, CSO sign-off, test digest. The acceptor cannot be the engineer who made the change.
### safety.safety_case [P4]
`safety_case_id`, versions: hazard register, control set, build digest, test runs, market release manifest. Signed by clinical lead.
### safety.clinical_safety_incident [P2 table, P4 process] (REGIONAL only)
Std-A. `ref_no` (INC; class CS), `source` (patient, provider, Zoiko, regulator, monitoring), `safety_impact` (suspected, actual, unknown), `subject_ref` (cell-local), `feature`, `build`, `partner`, `patient_care_owner` (mandatory), `incident_commander`, `reporting_clock_source` (country rule), `reportability_decision`, `severity_band` (see enum reconciliation), `evidence_hold`, `vigilance_determination`, `state` (DETECTED, TRIAGED, CONTAINMENT_IN_PROGRESS, CONTAINED, INVESTIGATION, CORRECTIVE_ACTION, VERIFIED_RECOVERY, CLOSED/MONITORING; plus POTENTIAL_HARM, CLINICAL_PROVIDER_NOTIFIED, ESCALATE, parallel REGULATORY_REVIEW). Never auto-closed on timeout; unknown harm stays open; kept after feature suspension. Cyber, clinical, complaint, vigilance, breach clocks tracked separately.
### safety.feature_suspension [P4]
POST `/v1/feature/{id}/suspend`: `emergency_authorizer`, `incident_id`, `signer`, `reason`, `evidence`, `effective_at`. Revokes feature permissions in the cell immediately. (Uses the generic `platform.hold`.)
### safety.release_gate_decision [P4]
Features x version x country x operator x data cell: `signatory`, `evidence_digest`, `expiry`, `permitted_population`, `clinical_setting`, `rollback_response`. Gates G0-G4. (Uses `platform.approval_decision`.)
### safety.duty_roster / safety.operating_model_schedule / safety.open_decision [P3]
Provider duty roster/escalation config (timezone, holidays, coverage, contingency, test results, severity-specific times set by provider). Legal operating-model schedule (contracting seller, controllers/processors, registered providers, clinical employer, device manufacturer, insurance holders, subcontractors, disclosure entity). Open decision register D-01.. (owner, disposition OPEN).
Events: `safety.feature.changed.v1`, `safety.incident.escalated.v1`.

---
## B. CYBERSECURITY (schema `security`) [09]

### security.threat_record [P4]
Std-A. `threat_id` (T01-T16 seeded), `capability_id`, `jurisdiction_id`, `cell_id`, `source_version`, `assets[]`, `actors[]`, `entry_point`, `trust_boundary`, `abuse_path`, `cia_impact`, `clinical_hazard_ref`, `privacy_risk_ref`, `business_impact`, `likelihood_basis`, `detectability`, `inherent_risk`, `controls[]`, `verification_tests[]`, `detection_rule_id`, `response_runbook_id`, `control_owner`, `residual_risk`, `residual_owner`, `approval_ref`, `last_reviewed_at`, `expiry_or_change_trigger`, `state` (open, mitigating, test_pending, accepted, blocked, retired).
### security.security_decision_audit [P1] → same as `evidence.audit_event` (single table, `request_class` distinguishes)
### security.security_alert [P4]
`alert_id`, `correlation`, `severity`, `affected_asset_pathway`, `triage_owner`, `confidence`, `clinical_safety_overlay`, `outcome` (confirmed → opens incident; false positive: reason + reviewer + rule tuning).
### security.security_incident [P4] (REGIONAL)
Std-A. `ref_no` (INC; class SEC), `finding_ref`, `discovery_time`, `awareness_time`, `affected_services[]`, `suspected_impact`, `tier` (P0-P3), `state` (DETECTED, TRIAGED, DECLARED, CONTAINING, LEGAL/CLINICAL + FORENSIC PRESERVE, ERADICATING, RECOVERING+VERIFY, CLOSED after independent review; REOPEN and COUNTRY FEATURE SUSPEND from any state), `owner`, `evidence_hold`, `affected_patients` (unknown/range), `affected_partners`, `containment`, `legal_regime`, `reportability` (OPEN/counsel), `deadline_at` (calculated after legal determination), notice tasks (regulator/partner/patient with owners + sign-offs). Evidence: forensic images, legal hold, chain of custody (restricted, regional). Runbooks IR-01..08 (ransomware, partner compromise, wrong-patient disclosure, malicious privileged user, cross-cell leak, forged ACK, DDoS, CI/CD compromise). Link table `incident_link` to `safety.clinical_safety_incident`.
### security.connector_suspension [P4]
POST `.../connectors/{id}/suspend`: `incident_ref`, `policy_version`, `approved_fallback_ref`, `approvals[]` → `status`, `partner_notification_ref`, `effective_at`.
### security.supply_chain_record [P4]
SBOM, signed artifact, provenance, deploy approver; `vulnerability_register` (owner, due date, exception with risk owner + expiry), release exception.
### security.vendor / subprocessor_inventory [P3]
Contract scope, breach-notice duty, audit rights, exit. (Shares `policy.subprocessor`.)
Events: SecurityPolicyDenied, CredentialRevoked, ConnectorQuarantined, IncidentDeclared, SecurityContainmentApplied, RecoveryVerified.
Privileged JIT grants and break-glass: see `policy.privileged_access_grant` and `trust.break_glass_session` (02).

---
## C. CLOUD / SRE / OPERATIONS (schema `ops`) [18]

### ops.slo [P4]
`slo_id`, `service`, `user_journey`, `measurement_point`, `data_source`, `numerator`/`denominator`, `percentile`/`window` (rolling 30d; burn 5m/1h/30d), `allowed_exclusions`, `owner`, `error_budget_rule`, `fallback`, `country_pack_version`, `query_version`. Observation: `window`, `good`, `error_budget_status` (WITHIN | EXHAUSTED | UNKNOWN), `source_health` (GOOD | PARTIAL | UNAVAILABLE). Missing telemetry = UNKNOWN, never green. Baselines (not contractual): admin read 99.9%/p95<750ms; admin RPO<=15min/RTO<=4h; 100% audit coverage. Clinical urgency tier set per safety case.
### ops.release_manifest [P4]
`code_digest`, `data_migration_rev`, `signed_pack`, `intended_purpose`, `compatibility_matrix`, `approval_ids`.
### ops.deployment_record [P4] [20]
`deploy_id`, `artifact`, `cell_id`, `cohort`, `feature_flags_hash`, `actor`, `window`, `rollback_ref`.
### ops.canary_stage [P4]
Stage (1/5/25/100% illustrative), timings, exposure set, signals, revert evidence.
### ops.migration_ledger / ops.backup_manifest / ops.restore_rehearsal / ops.kms_key_inventory [P1 for migrations, P4 rest]
Migration: `checksum`, `applied_version`. Backup manifest: `region`, `tenant`, `retention`, `key`, `success_hash`. Rehearsal: `dr_rehearsal_id`, measured RPO/RTO, `checksum`, `reconciliation_hash`, sign-off. KMS: key lifecycle, rotation, compromise/revocation tests.
### ops.runbook / ops.oncall_roster / ops.incident_timeline [P4]
Runbooks RB-01..10 (cell outage, lost queue, data loss/PITR, wrong-region routing, telemetry contaminated, credential compromise, partner outage, unsafe clinical release, PSP outage, cost). On-call/RACI, backup escalation with manual contacts, signed incident timeline.
### ops.failover_decision / ops.degradation_state / ops.kill_switch [P4]
Failover: version, local operator, key/backup proof, DNS change audit, approved whitelist. Degradation: green → degraded → read-only/limited → blocked, with capability flag, workload class, reason, time, restoration checks. Kill switch scopes: feature / cell / country-pack. Event `ops.cell.release.halted`.
### ops.capacity_forecast / ops.cost_record [P4]
Forecast with rate-card version; cell decommission/vendor-exit records.
### ops.safety_sentinel_metric [P4]
Unacknowledged handoff, stuck result, misrouted actor counts.
### ops.severity (lookup) [P1]
ONE enum table (conflict in README §6.2): `severity_code` + per-class mapping (SEV-C, SEV-1..4 from 18; SEV-0..3 from 20; P0..P3 from 09).

---
## D. VERIFICATION & TRACEABILITY (schema `quality`) [19]

Graph: Requirement → Hazard → Control → Test case → Execution → Evidence / Defect / Reviewer signature → Release attestation (n..m links). Evidence from another release tuple is not reusable.

### quality.requirement [P1]
`req_id`, `source_doc` + `source_version`, `clause`, `feature`, `jurisdiction_scope`, `verification_class`, `owner`, `approval`. Catalog with changed-source diffs.
### quality.test_case [P1]
Std-C. `test_id` (e.g. `V-D02@1.0`), `requirement_refs`, `control_refs`, `method`, `fixtures`, `steps`, `expected`, `severity` (e.g. BLOCKING_CLINICAL), `automation_mode`, `version`, `owner`, `source[]`. Families A-J: scope/country, data/privacy, identity, clinical risk, interoperability, workflow, payer/billing, security, availability, UX. 96 baseline cases (GHE-19); GHE-12, 20, 21, 23 have own acceptance sets (e.g. 84).
### quality.test_run [P1]
Append-only. `run_id`, `test_id`/`version`, `build_digest`, `pack_hash`, `cell`, `tenant_category`, `connector_protocol_version`, `started_at`, `ended_at`, `result` (PASS, FAIL, BLOCKED, NOT_RUN, INVALID). Missing provenance = INVALID; a retry cannot hide a prior failure.
### quality.defect [P2]
`defect_id`, `affected_version`, `severity` (P0/P1...), `reproduction`, `safety_flag`, `root_cause`, `resolution`, `retest_run_id` (new build digest), `approver`, `state` (DETECTED, TRIAGED, OWNED, FIXED, RETESTED, REVIEWED, CLOSED/REOPENED; non-waivable stop branch).
### quality.deviation [P4]
Noncritical only: scope/build, reason, compensating controls, `expires_at`, owner, revalidation trigger.
### quality.release_attestation [P4]
`release_id`, `scope_tuple`, `test_suite_hash`, `coverage`, `exception_ledger`, `reviewer_set`, `gate_result`, `valid_from/to`, `assessment_status` (NO_GO, CONDITIONALLY_REVIEWABLE, READY_FOR_GHE20), sign-offs (QA, ClinicalSafety, Security, Privacy, Operations), `baseline_catalog_count`, `applicable_mandatory_count`, `unresolved_risks[]`.
### quality.regulatory_applicability [P4]
Country, function, device?, provider?, QMS?, opinion date, counsel/regulator, conditions, expiry.
### quality.fixture_dataset / quality.control_evidence_matrix [P2]
Synthetic fixture lineage; control-to-evidence matrix (e.g. C-CLINICAL-ACK → V-D02/D03/F06); generated suite manifest per pack/cell/service/purpose; readiness package exporter.
### Junctions: `requirement_hazard`, `requirement_test`, `hazard_control`, `control_test`, `test_run_evidence`.

---
## E. GO-LIVE / PRODUCTION READINESS (global `gov` + regional) [20]

### gov.evidence_index (hash + pointer only) [P4]
Global side of `evidence.evidence_record`: `evidence_id`, `sha256`, `type`, `issuing_role`, `signer`, `issued_at`, `expires_at`, `tuple_digest`.
### gov.gate_decision [P4]
(= `platform.approval_decision` rows, gate G0-G4) `decision_id`, `gate`, `outcome` (NO_GO | G3_READY | G4_AUTHORIZED | PILOT_ACTIVE | SUSPENDED), `blocker_codes[]`, `waiver_ids[]`, `approvers`, `reasons`, `effect_time`, `approved_until`. Append-only; vetoes recorded. Role authorities with veto: sponsor, CTO, SRE, CSO, DPO, CISO, country regulatory, QA, partner lead.
### gov.readiness_exception [P4]
`exception_id`, `severity`, `control_id`, `risk_owner`, `expires_at`, `compensating_control`. Never for non-waivable conditions. Stop-ship conditions list with who can clear each.
### gov.capability_binding [P4]
`intended_purpose`, `clinical_protocol`, `partner_ids`, `device_status_ref`, `owner`.
### ops.operational_coverage [P4]
`coverage_id`, `roles`, `support_window`, `pager_tests`, `country_contacts`, `escalation_tree`.
### workflow.clinical_continuity_record [P4] (regional)
`workflow_ref`, `clinical_owner`, `contact_fallback`, `reconciliation_state`.
### ops.reconciliation_run (pilot) [P4]
`run_id`, `cell`, `workflow_version`, `pending_count`, `exception_count`, `signed_result`.
### ops.service_acceptance [P4]
`acceptance_id`, `regional_owner`, `rota_version`, `slo_result`, `defects`, `signature`.
### gov.dossier_manifest [P4]
Sections A-M: authority, intended purpose, contracts, data governance, security, verification, infrastructure, operations, financial, accessibility, rollout, sign-off, aftercare. `signed_dossier_manifest`, `vault_uri`. Other emitted evidence: readiness_snapshot, manifest_comparison, attestation_verified, transfer_policy_decision, partner_cert_id + tested_digest, security_attestation, safety_case_signature, slo_preflight_report, dr_rehearsal_id, sim_run_id, pager_drill, contact_drill, finance_attestation, a11y_record, cutover_plan_id, flag_plan, cohort_change + signal_snapshot, rollback_event + pending_cases_report, reconciliation_report, suspension_event (all `evidence_record` rows typed by `evidence_class`).

---
## F. CAPABILITY ACTIVATION (annex) [21]

Lifecycle: PROPOSED → SCOPING → EVIDENCE_PENDING → ASSESSMENT_READY → APPROVED_FOR_BUILD → VALIDATION_PENDING → VALIDATED → AUTHORISED_FOR_PILOT → ACTIVE → ON_HOLD / SUSPENDED → REVALIDATION_PENDING → RETIRING → RETIRED.

- `gov.capability_definition` and `gov.activation_instance`: see 02.
- `activation.responsibility_assignment` [P4]: `assignment_id`, `role`, `accountable_entity`, `licensed_professional_reference`, `obligation`, `acceptance_evidence`, `scope`, `valid_from/to`.
- `activation.intended_purpose_assessment` [P4]: `assessment_id`, market, function, output, user, patient impact, regulatory route, reviewer, date, expiry; needs local legal + clinical signature.
- `activation.control_binding` [P4]: `control_id`, `requirement`, `authority_doc`, `policy_version`, `enforcement_point`.
- `activation.data_flow_reference` [P4]: `flow_id`, controller/processor map, source, recipient, lawful-purpose id, transfer route, sensitivity. (FK to `policy.flow_revision`.)
- `activation.validation_run` [P4]: `test_ref`, `code_digest`, `pack_digest`, `environment`, `outcome`, `ran_at`.
- `activation.transition_record` [P4]: `prior_state`, `next_state`, `actor`, `policy_verdict`, `evidence_refs`, `at`; append-only with causal links.
- `activation.annex_instantiation` [P4]: controlled template fields (instance id + definition revision; R3 domains and journeys; intended purpose + exclusions; sponsor/operators; country/cell/tenant scope; clinical responsibility assignments; device/IVD/AI outcomes; DPIA and transfer refs; source-of-truth systems + GHE-22 evidence; workflow states + fallback; UI delta; billing controls; hazard log; test evidence + hashes; runtime policy + suspension owner; GHE-20 dossier). Decision BLOCKED until all evidence current.
- `activation.intake` [P4]: `intake_id`, `baseline_scope_digest`.
- Events: capability.activation.requested / denied, capability.activated, capability.suspended, capability.revalidation.required, capability.retired (envelope: event_id, activation_id, scope, version, causation_id, occurred_at, signature, reason_code).
- Hold: uses `platform.hold`; overrides all prior approvals.

---
## G. REGULATED CLINICAL AI / DEVICE (schema `ai`, D18). OFF BY DEFAULT [23, R3.0 §13]
Levels: A0 deterministic automation; A1 non-clinical language tools; A2 clinical documentation assist; A3 clinical decision/device AI (separate medical-device + AI regulatory qualification). Internal lanes A admin assist, B clinical-content assist, C decision-informing, D device-controlled, E agentic action.

### gov.ai_use_permit [P4]
`feature_id`, `intended_purpose_version`, `legal_entity_id`, `manufacturer_ref`, `market_pack_hash`, `region_cell`, `site_id`, `capability_id`, `model_digest`, `prompt_digest`, `knowledge_manifest_hash`, `tool_policy_hash`, `population_scope`, `clinical_owner_ref`, `reviewer_role`, `classification_opinion_ref`, `approval_refs[]`, `evidence_bundle_hash`, `expiry`, `permit_state` (PROPOSED → QUALIFICATION → DESIGN_CONTROL → VALIDATION → SANDBOX → SHADOW → RELEASE_REVIEW → AUTHORIZED; from AUTHORIZED: SUSPENDED, CHANGE_REVIEW, RETIRED; SUSPENDED → CORRECTIVE_ACTION → REVALIDATION → RELEASE_REVIEW; RETIRED terminal). Re-evaluated at each inference and tool call. Model/prompt/knowledge/tools immutable by digest; no mutable provider alias.
### ai.ai_request [P4] (PHI, regional)
`request_id`, `actor_identity_ref`, `delegated_authority_ref`, `patient_link_ref`, `care_relationship_ref`, `workflow_ref`, `purpose_code`, `country`, `region_cell`, `model_version`, `input_source_refs[]`, `request_policy_ref`, `requested_tool_actions[]`, `correlation_id`.
### ai.ai_response_evidence [P4] (PHI, regional)
`output_id`, `request_id`, `output_digest`, `model_digest`, `prompt_digest`, `retrieval_source_versions[]`, `validator_flags[]`, `clinical_reviewer_ref`, `safety_case_ref`, `recorded_at`, `emitted_action_refs[]`, `decision` (ALLOWED | HUMAN_REVIEW | REJECTED | QUARANTINED).
### Registries [P4]
- `gov.model_card`: name, provider, version, digest, weights/endpoint/host region, SBOM, known limits, license, change policy, lineage hashes, evaluation package.
- `gov.dataset_card`: origin, consent/legal basis, use rights, subject class (patient/synthetic/public), cell, retention, training rights, DPIA decision; `population_report`.
- `gov.knowledge_source`: guideline id/version/date/publisher, approved country, expiry, retracted?, allowed users, citation id.
- `ai.prompt_policy`: prompt hash, trust labels, top-k, PHI rules.
- `ai.tool_policy`: agent identity, tool allowlist (whitelist-only), purpose/patient/country limits, write actions needing named approver, signer refs.
- `ai.agent_action_approval`: human actor, payload hash, 2-person where required; execution graph (max steps, time/cost budget, dedupe key); rollback journal (compensation).
- `ai.qualification_decision`: country, regime, classification, device?, route, manufacturer, authorization expiry, counsel signature. `ai.manufacturer_register` (legal entity, QMS scope, design history, SOP, vigilance).
- `ai.evaluation`: locked suite, baseline, safety metrics, subgroups, error types, open failures, confidence intervals; signed test artifacts; case ids; attack casebook; human-factors report; shadow-run ("no-act") evidence.
- `ai.monitoring`: drift/subgroup/error monitor (model version, input drift, safety metrics; breach → pause/investigate); drift incident; model/vendor change impact (PCCP/recertify/retrain/retest).
- `ai.safety_event`: complaint/adverse event, causality, patient safety, model version, region, reportability (local), clock owner, signed vigilance determination.
- `ai.suspension`: scope (feature/model/market/site), stop inference/tool acts/routes, fallback, continuity attestation. `ai.postmarket_bundle` (regional, sealed), `ai.release_permit` (approvals + expiry), `ai.retirement_certificate`.
- AI hazard seed HAI-01..16: fabricated fact, missed urgent sign, unapproved medical purpose, wrong patient, unauthorized export, prompt injection, automation bias, silent model update, subgroup harm, data leakage, misleading confidence, agent overreach, lost emergency path, version mismatch, unreported device event, misuse outside indication.
- Release council: exact bundle x feature x site x market x region; signers Legal, Clinical, QA, SRE.
- Rules: No PHI to a third-party model endpoint or external training without contract/jurisdiction/security/privacy approval; no hallucinated provider/availability; RAG answers carry source ids, dates, permissions; agents never order medication or release critical results; model output is never presented as a verified source clinical fact.
