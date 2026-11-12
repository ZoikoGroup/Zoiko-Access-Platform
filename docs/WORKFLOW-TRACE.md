# Workflow engine: the specification's 60 acceptance tests, checked against what is built

Written 2026-11-11 by the developer, from GHE-12 section 12. Each row is a judgement made by reading the test and the code, not an automatic result; "Met" means one of our own automated tests proves the behaviour. The specification's own tests have **not** been run as written (they need partners, country packs and a regional setup that do not exist). This is the developer's reading and has not been reviewed by anyone else.

**Totals: 18 met, 18 partly, 24 not built (of 60).**

Most of the "not built" rows need things the engine cannot invent: partners that return results, country packs applied at run time, patient matching, regions and failover, and the release gate. They are listed so a reviewer can see exactly where the line is.

| Test | Type | What the specification tests | Status | Why, and where it is tested |
|---|---|---|---|---|
| WF-T01 | Activation | New workflow is denied when country pack is draft, expired or suspended. | Not built | Definitions do not look at country packs, so a draft, expired or suspended pack cannot stop a start. |
| WF-T02 | Activation | A workflow template cannot activate without designated clinical fallbacks for a safety-relevant step. | Partly | Clinical paths cannot be published at all while the clinical switch is off; there is no check for named clinical fallbacks. |
| WF-T03 | Activation | Run same kernel using two deliberately distinct synthetic country packs. | Not built | The engine does not read packs, so two packs cannot make it behave differently. |
| WF-T04 | Activation | Definition N+1 is deployed while N workflows are active. | Met | test_orchestration.py: a change is a new version and running instances stay on theirs. |
| WF-T05 | Activation | Remove old definition deployment while an N instance is in flight. | Partly | test_orchestration.py: a retired definition keeps running instances going. There is no delete path, and the database refuses deleting a definition an instance uses (not tested). |
| WF-T06 | Activation | A non-clinical J4 uses its valid payer closure predicate. | Met | test_orchestration.py: a non-clinical path never invents a review step. There is no payer-specific closure predicate. |
| WF-T07 | Activation | Unapproved regulated feature toggle is set true manually. | Not built | The clinical switch only decides whether a definition can be published. Nothing checks it, or any approval, when a workflow runs. |
| WF-T08 | Identity | Authenticated user without required patient relationship starts J2. | Partly | Only staff may start one. Subjects are opaque references, so there is no patient-relationship check. |
| WF-T09 | Identity | A workflow callback supplies a different cell_id or tenant_id. | Partly | Tenant and cell come from the login or the partner key, never from the body, but no test sends a conflicting value. |
| WF-T10 | Identity | Revoked delegation sends an otherwise valid step request. | Not built | Engine actors are staff; there is no delegation in a workflow step. |
| WF-T11 | Identity | Patient match is ambiguous when result arrives. | Not built | No patient matching in the engine. |
| WF-T12 | Identity | Provider credential expires between request and review. | Not built | Review attestations are references; no provider credential is checked. |
| WF-T13 | Lifecycle | Same CreateWorkflow request is retried with same key and body. | Partly | A repeated start is refused with a conflict (one live workflow per subject) rather than answered with the original response. |
| WF-T14 | Lifecycle | Same key reused with differing payload. | Partly | test_orchestration.py: the same idempotency key with a different target is a conflict. Start has no idempotency key. |
| WF-T15 | Lifecycle | Concurrent transitions use stale expected_version. | Met | test_orchestration.py: a stale expected version is a conflict and changes nothing. Tested one after the other, not truly in parallel. |
| WF-T16 | Lifecycle | Unauthorized actor requests CANCELLED state. | Met | test_orchestration.py: only the owner or an administrator may move; others are refused and nothing changes. |
| WF-T17 | Lifecycle | Simulated partner transport ACK attempts CLINICAL_REVIEWED. | Met | test_orchestration.py: every transport and read receipt kind is refused as evidence. |
| WF-T18 | Lifecycle | HL7 ACK arrives without a named accepting clinical owner. | Met | test_orchestration.py: acceptance needs an acceptance attestation; a transport acknowledgement is refused. |
| WF-T19 | Lifecycle | Provider confirms work began but has not produced result. | Met | test_orchestration.py: each step needs its own evidence kind and cannot be skipped. |
| WF-T20 | Lifecycle | Signed result correlation does not match patient/order. | Not built | No result correlation to a patient or order. |
| WF-T21 | Lifecycle | Verified result and valid review attestation match version. | Partly | test_orchestration.py: a review step passes once with the right evidence kind. The attestation itself and its source version are not verified. |
| WF-T22 | Lifecycle | Signed review arrives with wrong source version. | Not built | No source versions on results. |
| WF-T23 | Lifecycle | Patient opened report in app but clinician has not reviewed. | Met | test_orchestration.py: viewed, downloaded and read receipts are refused as evidence. |
| WF-T24 | Lifecycle | Follow-up email was delivered without receiving provider acceptance. | Met | test_orchestration.py: email sent and notification delivered are refused as evidence. |
| WF-T25 | Lifecycle | All journey-specific closure predicates and evidence pass. | Met | test_orchestration.py: the full path closes once with a closure digest and an intact history. |
| WF-T26 | Lifecycle | A required safety obligation remains unowned. | Partly | test_orchestration.py: an open exception or an escalation waiting for a person blocks closing. A general 'unowned safety obligation' is not modelled. |
| WF-T27 | Lifecycle | A pending partner action exists when cancellation occurs. | Not built | No partner actions to reconcile on cancellation. |
| WF-T28 | Lifecycle | Original report receives a clinically important correction. | Not built | No report corrections or source versions. |
| WF-T29 | Evidence | Partner callback originates from unapproved endpoint/key. | Partly | Partner messages with a bad signature are refused (DEV-131) and a valid one from a disallowed kind is held; they never advance a workflow, because nothing connects them yet. |
| WF-T30 | Evidence | Attestation by valid but wrong-scope clinician arrives. | Not built | No attester scope or relationship check. |
| WF-T31 | Evidence | Audit event source-time precedes processing-time due to delayed delivery. | Not built | A step records one time, not source time and processing time. |
| WF-T32 | Evidence | Evidence ledger is missing a transition reference. | Met | test_orchestration.py: a workflow whose state, overlay or version disagrees with its history is found and frozen (423). A break in the chain itself uses the shared chain check. |
| WF-T33 | Evidence | Evidence proof is tampered after storage. | Partly | test_orchestration.py: tampering with the stored workflow is found and frozen, and the database refuses editing a step. No incident is raised automatically. |
| WF-T34 | Evidence | Closure export requested by employer without clinical disclosure rights. | Not built | No closure export. |
| WF-T35 | Evidence | Record retention/deletion request conflicts with a lawful hold. | Not built | No retention or lawful-hold handling. |
| WF-T36 | Evidence | Rebuild materialized status from event history. | Met | test_orchestration.py: state, overlay and version are rebuilt from the history alone and compared. |
| WF-T37 | Timeouts | Provider acceptance timer expires without acknowledgment. | Met | test_orchestration.py: an expired timer marks it at risk and opens an escalation for the owner; nothing accepts automatically. |
| WF-T38 | Timeouts | Clinical reviewer unreachable via first channel. | Met | test_orchestration.py: an unsuccessful try is counted and can hand the escalation to a fallback person; the workflow stays where it was. |
| WF-T39 | Timeouts | A timer fires after the step was already completed. | Met | test_orchestration.py: a timer for a step already left does nothing. |
| WF-T40 | Timeouts | Deadline crosses local DST clock change. | Not built | Timers are plain minutes in UTC; calendar and time-zone rules are not supported, and DST is not tested. |
| WF-T41 | Timeouts | Escalation channel says delivered but no human reach proof. | Met | test_orchestration.py: only a person recording that someone was reached closes an escalation. |
| WF-T42 | Timeouts | Safety-relevant queue loses designated on-call owner. | Not built | Nothing notices when an owner is deactivated or leaves. |
| WF-T43 | Retries | Crash after domain commit before outbox publication. | Not built | The engine writes no outbox events, so there is nothing to heal. |
| WF-T44 | Retries | Bus redelivers same callback multiple times. | Partly | test_orchestration.py: a repeated idempotency key makes one transition, and a repeated partner delivery id is stored once (DEV-131). The two are not connected. |
| WF-T45 | Retries | Remote provider side-effect outcome unknown. | Partly | A 'reconciling' health state exists and blocks closing, but nothing sets it from an external call. |
| WF-T46 | Retries | Remote cancellation not acknowledged. | Not built | No external cancellation. |
| WF-T47 | Retries | Poison event fails parsing repeatedly. | Partly | A partner message that cannot be parsed is held and shown to staff (DEV-131); nothing feeds it into the engine. |
| WF-T48 | Retries | Delayed event arrives after successor event. | Met | test_orchestration.py: only the next state is accepted and going back is refused, so a late event cannot move it backwards. |
| WF-T49 | Regional | Same reference identifier exists in another region. | Not built | One region only; no cross-region identifiers. |
| WF-T50 | Regional | Cross-border receiver is unapproved while source is valid. | Not built | No cross-border handling. |
| WF-T51 | Regional | Global observability exporter requests workflow payload. | Partly | Metrics carry route patterns and counts only, no payloads (DEV-130, tested). No exporter or workflow payload exists to test against. |
| WF-T52 | Regional | Regional failure triggers failover to unauthorized region. | Not built | No failover. |
| WF-T53 | Operations | Admin tries to enter clinical attestation on partner behalf. | Met | test_orchestration.py: an administrator cannot enter a review or a handoff for the accountable owner; they must first take responsibility, which is recorded. |
| WF-T54 | Operations | Operator resolves patient-match exception without evidence. | Partly | test_orchestration.py: clearing an exception needs a written reason but not evidence. |
| WF-T55 | Operations | Operator presses replay for non-idempotent unknown action. | Not built | There is no replay command for external actions. |
| WF-T56 | Operations | User of assistive technology reviews workflow status. | Partly | The accessibility scan passes on the Workflow engine page. No assistive-technology review by a person. |
| WF-T57 | Operations | Patient locale lacks approved emergency text. | Not built | No locale-specific emergency text. |
| WF-T58 | Operations | SRE tests regional restore with active timer and open safety handoff. | Partly | All state, history and timers are in the database, but no restore with an active timer has been rehearsed. |
| WF-T59 | Operations | Deactivated partner contract leaves ongoing patients with steps. | Partly | test_orchestration.py: retiring a definition stops new starts. There is no wind-down or handoff plan. |
| WF-T60 | Operations | Workflow QA matrix contains a missing clinical-safety blocker. | Not built | The release gate and the requirements matrix (GHE-19) are not built. |
