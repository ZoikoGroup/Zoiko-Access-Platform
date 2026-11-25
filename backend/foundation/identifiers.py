"""The catalogue of identifiers: every kind of id this product stores, what it names, what shape
it has, where it is unique, and whether it may ever leave the cell.

A row has an internal `id` (never leaves the cell) and a `public_id` (foundation/ids.py). Modules
never import each other's models, so they point at each other's rows by public id, in fields named
`*_ref`. Besides those there are a few other identifier-like values: the cell and tenant a row
belongs to, country pack names, numbers people read out on the phone, keys that stop a thing being
done twice, and numbers other organisations gave us.

Each KIND of identifier is listed once in IDENTIFIERS. FIELDS says which kind each field name holds
(many field names hold the same kind: `created_by_ref`, `approved_by_ref` and `actor_ref` all name
a login). A test walks every model and every event in the event catalogue and fails if a field
that looks like an identifier is not listed here, or this list names something nothing uses, so
the catalogue cannot silently go stale.

Words used below:
  scope        where the value is unique and means something: "cell" (only inside this cell),
               "tenant" (only inside one customer), "global" (anywhere).
  leaves_cell  whether the value may be carried out of the cell, in an event (foundation/outbox.py:
               events carry references only) or in a message to a partner.
  from_personal_data
               whether the value is made from facts about a person (a name, an email address, a
               phone number, a birth date). No identifier may be: an identifier gets copied into
               logs, events and URLs, and personal data must not travel that way. Keyed
               fingerprints of personal data (used to find duplicates) exist, but they are kept
               out of this catalogue on purpose: see NOT_IDENTIFIERS.
  personal     whether the value, on its own, picks out a person to someone outside Zoiko (a
               payer's member number does). Such values must stay in the cell.

This module imports nothing from `modules` (an import-linter contract forbids it)."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

Scope = str  # "cell" | "tenant" | "global"
SCOPES = frozenset({"cell", "tenant", "global"})

# The shapes this product makes itself. Anything given to us by someone else has no fixed shape.
PUBLIC_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")                     # secrets.token_urlsafe(16)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ACTOR = re.compile(r"^(?:[A-Za-z0-9_-]{22}|system)$")               # a login, or the system itself
CELL = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,31}$")                     # as foundation/cell_context.py
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,60}$")                        # e.g. "gb-care-navigation"
REQUEST = re.compile(r"^[A-Za-z0-9._-]{8,64}$")                     # foundation/request_context.py


@dataclass(frozen=True)
class Kind:
    """One kind of identifier. `pattern` is set only when the shape is fixed, so check() can tell a
    good value from a bad one; for values other people give us it is None."""

    names: str
    format: str
    scope: Scope
    leaves_cell: bool
    pattern: re.Pattern[str] | None = None
    from_personal_data: bool = False
    personal: bool = False
    in_models: bool = True   # False: carried in events only, never stored under this name


def _ref(names: str, *, leaves: bool = True, in_models: bool = True) -> Kind:
    return Kind(names=names, format="public id (22 URL-safe characters)", scope="global",
                leaves_cell=leaves, pattern=PUBLIC_ID, in_models=in_models)


IDENTIFIERS: dict[str, Kind] = {
    # ---------------------------------------------------------------- every row's own ids
    "id": Kind(
        names="the row itself, inside the database (primary keys and foreign keys)",
        format="UUID (version 7 from foundation/ids.py; a few tables use version 4)",
        scope="cell", leaves_cell=False, pattern=UUID),
    "public_id": _ref("the row itself, in URLs, APIs, events and links"),
    # ---------------------------------------------------------------- where a row lives
    "tenant_id": Kind(
        names="the customer a row belongs to (trust Tenant.id)", format="UUID",
        scope="cell", leaves_cell=False, pattern=UUID),
    "cell_id": Kind(
        names="the cell (one regional deployment) a row lives in",
        format="upper-case letters, digits and dashes, 2-32 characters",
        scope="global", leaves_cell=False, pattern=CELL),
    "tenant_ref": _ref("a customer or partner (trust Tenant.public_id)"),
    # ---------------------------------------------------------------- who
    "actor_ref": Kind(
        names="a login (trust Actor.public_id), or \"system\" for automatic steps",
        format="public id, or the word \"system\"", scope="global", leaves_cell=True,
        pattern=ACTOR),
    "person_ref": _ref("a patient or other person cared for (trust Person.public_id)"),
    "organization_ref": _ref("a clinic, practice, payer or employer (trust Organization)"),
    # ---------------------------------------------------------------- things, by public id
    "service_ref": _ref("a bookable service (catalog Service)"),
    "location_ref": _ref("a place services are given (catalog Location)"),
    "booking_ref": _ref("a booking (scheduling Booking)"),
    "permit_ref": _ref("an AI use permit (ai AiUsePermit)", leaves=False),
    "request_ref": _ref(
        "an AI request (ai AiRequest) in the AI tables; an exchange request in events"),
    "hazard_ref": _ref("a clinical safety hazard (safety Hazard)", leaves=False),
    "note_ref": _ref("a consultation note (clinicalnote ClinicalNote)", leaves=False),
    "claim_ref": _ref(
        "a claim to a payer (claim Claim), carried in an event only", in_models=False),
    "email_ref": _ref("a queued email (foundation QueuedEmail)", leaves=False),
    "object_ref": Kind(
        names="any record, by public id, where the table records several kinds of thing "
              "(the audit trail, the outbox)",
        format="public id (stored in a wider column)", scope="global", leaves_cell=True,
        pattern=PUBLIC_ID),
    "subject_ref": Kind(
        names="what a work item, workflow or access decision is about (a booking, referral, "
              "thread or other record)",
        format="opaque reference, 4-64 characters, never a name", scope="global",
        leaves_cell=True),       # a record's reference, like object_ref (connector events, DEV-217)
    "decision_ref": Kind(
        names="the access decision that allowed a workflow step (policy AccessDecision)",
        format="opaque reference, up to 40 characters", scope="cell", leaves_cell=False),
    # Carried in events (foundation/event_catalog.py) but stored as `public_id` on their own table.
    "slot_ref": _ref("a bookable slot (scheduling Slot)", in_models=False),
    "referral_ref": _ref("a referral (referral Referral)", in_models=False),
    "entry_ref": _ref("a waiting list entry (waitlist WaitEntry)", in_models=False),
    "thread_ref": _ref("a message thread (messaging Thread)", in_models=False),
    "case_ref": _ref("a data rights case (rights RightsCase)", in_models=False),
    "grant_ref": _ref("a family or delegation grant (trust ProxyGrant)", in_models=False),
    "assignment_ref": _ref("a role given to a login (trust RoleAssignment)", in_models=False),
    "instance_ref": _ref("a running workflow (orchestration WorkflowInstance)", in_models=False),
    "consent_ref": _ref(
        "a consent a person gave or withdrew (consent ConsentGrant)", in_models=False),
    "client_ref": _ref("an API client (developer ApiClient)", leaves=False),
    "coverage_ref": _ref("an insurance policy (coverage Coverage)", leaves=False),
    "recipient_ref": _ref(
        "who information about a patient was sent to: a clinic organization or another "
        "customer (evidence Disclosure)", leaves=False),
    # ---------------------------------------------------------------- numbers people read out
    "ref_no": Kind(
        names="a case number staff and patients can read out (DSR-, XCH-, M-0007, hazard codes)",
        format="short code; each table makes its own", scope="tenant", leaves_cell=True),
    # ---------------------------------------------------------------- names of configuration
    "pack_ref": Kind(
        names="a country pack (jurisdiction CountryPack), e.g. \"gb-care-navigation\"",
        format="lower-case slug, 3-61 characters", scope="global", leaves_cell=True,
        pattern=SLUG),
    "definition_key": Kind(
        names="a workflow definition (orchestration WorkflowDefinition)",
        format="short code", scope="global", leaves_cell=True),
    "feature_id": Kind(
        names="an AI feature a permit allows", format="lower-case dotted code",
        scope="global", leaves_cell=False),
    "release_ref": Kind(
        names="a product release being checked for go-live (readiness Release)",
        format="lower-case slug", scope="global", leaves_cell=False),
    "req_id": Kind(
        names="a requirement in a specification (traceability Requirement)",
        format="the specification's own id, up to 24 characters", scope="global",
        leaves_cell=False),
    "key_id": Kind(
        names="which signing secret signed something (never the secret itself)",
        format="short code, e.g. \"whk_1a2b3c4d\"", scope="tenant", leaves_cell=True),
    # ---------------------------------------------------------------- tracing and delivery
    "event_id": Kind(
        names="one published event (foundation OutboxEvent)", format="UUID (version 4)",
        scope="global", leaves_cell=True, pattern=UUID),
    "webhook_delivery_id": Kind(
        names="one attempt series to deliver an event to a partner's webhook",
        format="UUID (version 4)", scope="global", leaves_cell=True, pattern=UUID),
    "partner_delivery_id": Kind(
        names="a partner's own id for an event it sent us (so a repeat is ignored)",
        format="8-64 letters, digits, dots, dashes, underscores", scope="tenant",
        leaves_cell=False),
    "request_id": Kind(
        names="one inbound request or job run, to tie log lines and records together",
        format="8-64 letters, digits, dots, dashes, underscores", scope="cell",
        leaves_cell=False, pattern=REQUEST),
    "session_ref": Kind(
        names="a login session, as a fingerprint (never the session token)",
        format="up to 32 characters", scope="cell", leaves_cell=False),
    "idempotency_key": Kind(
        names="a caller's own key that stops the same step being done twice",
        format="caller's choice, up to 64 characters", scope="tenant", leaves_cell=False),
    "outbox_key": Kind(
        names="the outbox's ordering and duplicate keys, built from event types and public ids",
        format="\"<kind>:<public id>...\", up to 128 characters", scope="cell",
        leaves_cell=False),
    # ---------------------------------------------------------------- numbers others gave us
    "operation_ref": _ref("an operation Zoiko asked a partner system to perform (connector "
                          "ExternalOperation)", in_models=False),
    "connector_key": Kind(
        names="which partner connector (adapter) an operation goes through, e.g. \"simulator\"",
        format="short code", scope="global", leaves_cell=False),
    "external_ref": Kind(
        names="a number another organisation gave us: a payment reference, a partner's result "
              "number, a clinic's order number, a regulator's registration, a signed artefact",
        format="as given, checked by the module that stores it", scope="tenant",
        leaves_cell=True),
    "proof_ref": Kind(
        names="the clinic's own reference to the proof it saw before allowing a family member "
              "or delegate to act (never the document itself)",
        format="as given, 4-64 characters", scope="tenant", leaves_cell=False, personal=True),
    "clinical_record_id": Kind(
        names="a clinic's own id for a patient's record in its clinical system, kept by a "
              "clinical reference (DEV-041) so the care team can find the record there",
        format="as given, 1-64 characters from A-Z a-z 0-9 . _ : - with at least one digit",
        scope="tenant", leaves_cell=False, personal=True),
    "member_ref": Kind(
        names="a person's member or staff number at a payer or employer",
        format="as given by the payer or employer, up to 40 characters", scope="tenant",
        leaves_cell=False, personal=True),
}

# Field name -> the kind it holds. A field named after its kind (`person_ref`) is listed too, so
# this is the whole list of identifier field names.
FIELDS: dict[str, str] = {
    "id": "id", "public_id": "public_id", "tenant_id": "tenant_id", "cell_id": "cell_id",
    "from_tenant_ref": "tenant_ref", "to_tenant_ref": "tenant_ref", "customer_ref": "tenant_ref",
    "employer_tenant_ref": "tenant_ref", "clinic_tenant_ref": "tenant_ref",
    "person_ref": "person_ref",
    "organization_ref": "organization_ref", "from_organization_ref": "organization_ref",
    "destination_organization_ref": "organization_ref",
    "supersedes_ref": "note_ref", "superseded_by_ref": "note_ref", "claim_ref": "claim_ref",
    "service_ref": "service_ref", "location_ref": "location_ref",
    "booking_ref": "booking_ref", "related_ref": "booking_ref",
    "rescheduled_from_ref": "booking_ref",
    "permit_ref": "permit_ref", "request_ref": "request_ref", "hazard_ref": "hazard_ref",
    "email_ref": "email_ref", "object_ref": "object_ref", "aggregate_ref": "object_ref",
    "subject_ref": "subject_ref", "decision_ref": "decision_ref",
    "slot_ref": "slot_ref", "referral_ref": "referral_ref", "entry_ref": "entry_ref",
    "thread_ref": "thread_ref", "case_ref": "case_ref", "grant_ref": "grant_ref",
    "assignment_ref": "assignment_ref", "instance_ref": "instance_ref",
    "consent_ref": "consent_ref", "client_ref": "client_ref",
    "ref_no": "ref_no", "claim_no": "ref_no", "pack_ref": "pack_ref",
    "definition_key": "definition_key",
    "feature_id": "feature_id", "release_ref": "release_ref", "req_id": "req_id",
    "key_id": "key_id", "signing_key_id": "key_id",
    "event_id": "event_id", "source_event_id": "event_id",
    "request_id": "request_id", "correlation_id": "request_id", "causation_id": "request_id",
    "session_ref": "session_ref",
    "idempotency_key": "idempotency_key", "start_key": "idempotency_key",
    "ordering_key": "outbox_key", "dedupe_key": "outbox_key",
    "payment_ref": "external_ref", "result_ref": "external_ref", "subject_token": "external_ref",
    "authority_ref": "external_ref", "reference": "external_ref", "partner_ref": "external_ref",
    "connector_key": "connector_key", "operation_ref": "operation_ref",
    "proof_ref": "proof_ref", "external_id": "clinical_record_id",
    "member_ref": "member_ref", "member_id": "member_ref", "member_ref_enc": "member_ref",
    "coverage_ref": "coverage_ref", "source_ref": "external_ref",
    "record_ref": "object_ref", "recipient_ref": "recipient_ref",
}
# Every name for a login: who did, approved, checked, owns or watches something.
FIELDS.update(dict.fromkeys((
    "actor_ref", "delegate_ref", "acknowledged_by_ref", "added_by_ref", "approved_by_ref",
    "approver_ref", "approver_refs", "assessed_by_ref", "assigned_to_ref", "attendance_by_ref",
    "author_ref", "blocked_owner_ref", "booked_by_ref", "cancelled_by_ref", "changed_by_ref",
    "checked_by_ref", "clinical_owner_ref", "closed_by_ref", "consent_by_ref", "signed_by_ref",
    "created_by_ref", "decided_by_ref",
    "ended_by_ref", "fallback_ref", "first_reviewer_ref", "handoff_by_ref", "hold_by_ref",
    "identity_checked_by_ref", "joined_by_ref", "on_hold_owner_ref", "owner_ref",
    "proposed_by_ref", "provider_ref", "published_by_ref", "recorded_by_ref", "referrer_ref",
    "requested_by_ref", "resolved_by_ref", "responded_by_ref",
    "responsible_ref", "reviewed_by_ref", "reviewer_ref", "set_by_ref", "signer_ref",
    "started_by_ref", "updated_by_ref", "viewer_ref", "withdrawn_by_ref",
    "enrolled_by_ref", "certified_by_ref", "live_by_ref",          # DEV-230
    "merged_by_ref", "unmerged_by_ref",                            # identity merge
    "prioritized_by_ref",                                          # waiting-list priority
    "uploaded_by_ref",                                             # message attachments
    "deputy_ref",                                                  # DEV-202
    "delivered_by_ref", "replaced_by_ref",                         # DEV-325
    "death_recorded_by_ref",                                       # DEV-333
), "actor_ref"))

# Where one field name means different things in different tables: (app_label.Model, field).
MODEL_FIELDS: dict[tuple[str, str], str] = {
    ("integration.WebhookDelivery", "delivery_id"): "webhook_delivery_id",
    ("integration.InboundEvent", "delivery_id"): "partner_delivery_id",
    ("integration.FhirQuarantine", "delivery_id"): "partner_delivery_id",
    # a payment provider's own reference for a payment, not a clinician (DEV-139 skeleton)
    ("billing.PaymentAttempt", "provider_ref"): "external_ref",
}

# Fields shaped like identifiers that are not identifiers, and why. A keyed fingerprint of personal
# data finds duplicates; it must never be used to name, link or look up a record from outside.
NOT_IDENTIFIERS: dict[str, str] = {
    "birth_key": "keyed fingerprint of a birth date (crypto.blind_index), for duplicate finding",
    "phone_key": "keyed fingerprint of a phone number (crypto.blind_index), for duplicate finding",
    "member_key": "keyed fingerprint of a payer's member number (crypto.blind_index), for lookup "
                  "without keeping the number in the clear (DEV-216)",
    "code_key": "keyed fingerprint of a one-time programme code (crypto.blind_index), so the code "
                "itself is never kept (DEV-312)",
    "attempt_no": "a counter of email send attempts",
    "secret_token": "a webhook signing secret, kept encrypted; a secret, not an identifier",
    "login_actor_id": "a login's internal id, kept so an identity merge can be undone; it never "
                      "leaves the cell",
    "row_id": "the id of a row an identity merge moved, so unmerge can move it back",
}


def kind_of(field_name: str, model: str | None = None) -> str | None:
    """The kind of identifier a field holds, or None if it is not a listed identifier. `model`
    ("app_label.Model") is needed only for the few names that mean different things per table."""
    if model is not None and (model, field_name) in MODEL_FIELDS:
        return MODEL_FIELDS[(model, field_name)]
    return FIELDS.get(field_name)


def check(kind: str, value: object) -> bool:
    """True when `value` has the fixed shape of `kind`. Only kinds this product makes itself have
    a fixed shape; asking about any other kind is a mistake and raises ValueError."""
    pattern = IDENTIFIERS[kind].pattern
    if pattern is None:
        raise ValueError(f"{kind} has no fixed format")
    if isinstance(value, uuid.UUID):
        value = str(value)
    return isinstance(value, str) and pattern.fullmatch(value) is not None


_SEPARATORS = frozenset(" -._:/")


def contains_nhs_number(text: object) -> bool:
    """True when `text` holds a run of exactly ten digits (spaces, dashes, dots, colons, slashes or
    underscores between them allowed) that passes the NHS number check digit (modulus 11). Used to
    keep a patient's national number out of values that leave Zoiko, such as the order number and
    short description sent to a partner (DEV-222). A random ten-digit number passes about one time
    in eleven, so this refuses a few innocent numbers; that is the safe side."""
    if not isinstance(text, str):
        return False
    runs: list[str] = []
    current: list[str] = []
    for ch in text + "x":
        if ch.isdigit():
            current.append(ch)
        elif ch in _SEPARATORS and current:
            continue
        else:
            if current:
                runs.append("".join(current))
            current = []
    for run in runs:
        if len(run) != 10:
            continue
        total = sum(int(d) * (10 - i) for i, d in enumerate(run[:9]))
        check = 11 - total % 11
        check = 0 if check == 11 else check
        if check != 10 and check == int(run[9]):
            return True
    return False
