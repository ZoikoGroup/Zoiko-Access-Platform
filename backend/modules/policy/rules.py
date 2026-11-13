"""The access rules as data (docs/schema 07 "Data access by role"). Edit here, not in the engine.

Anything not listed is DENIED. Each rule says which verbs a role may use on a resource class
and what must also be true:

    ANY    nothing more (public catalog data)
    ORG    the actor holds that role at the organization named in the request
    REL    a verified care relationship with the patient exists  (modules/trust/people.py)
    OWN    the patient is the actor themselves                    (modules/trust/people.py)
    PROXY  a verified delegation from the patient exists          (modules/trust/family.py)

Known limit (conformance audit, DEV-104): ORG compares only the role's organization with the one
asked about; it does not yet check that a given PATIENT belongs to that organization.
"""

from __future__ import annotations

POLICY_VERSION = "p1-2026-10-proposed"

# Which part of a guardian's grant covers each class of data (GHE-06 AT-18, DEV-110). A class not
# listed (the dependent's own name) needs no scope. "records" is never granted yet: the
# specification's screen marks it BLOCK until a market rule and evidence process exist.
PROXY_SCOPE_OF = {
    "booking": "appointments", "workflow": "appointments",
    "comms": "messages", "referral": "referrals", "coverage": "insurance",
    "diagnostic": "records", "medication": "records", "billing": "records",
}

VERBS = ("read", "write", "attest")

RESOURCE_CLASSES = (
    "person",
    "booking",
    "referral",
    "workflow",
    "diagnostic",
    "attestation",
    "medication",
    "coverage",
    "billing",
    "comms",
    "evidence",
    "catalog",
    "partner",
    "analytics",
    "ops",
    "directory",
    "platform",
    "staff",
    "rights",
    "integration",
    "program",
)

PURPOSES = (
    "SELF_SERVICE",
    "DIRECT_CARE",
    "CONTINUITY_OF_CARE",
    "OPERATIONS",
    "PAYMENT_COVERAGE",
    "PROGRAM_ADMIN",
    "INTEGRATION",
    "SUPPORT",
    "SAFETY_REVIEW",
)

# Purposes under which a person may only READ. "Continuity of care" is following up earlier
# care; writing (referring, booking, deciding) is direct care (DEV-111).
READ_ONLY_PURPOSES = frozenset({"CONTINUITY_OF_CARE"})

# role -> purposes it may ever act under
ROLE_PURPOSES: dict[str, frozenset[str]] = {
    "R01": frozenset({"SELF_SERVICE"}),
    "R02": frozenset({"SELF_SERVICE"}),
    "R03": frozenset({"DIRECT_CARE", "CONTINUITY_OF_CARE"}),
    "R04": frozenset({"OPERATIONS"}),
    "R05": frozenset({"OPERATIONS", "CONTINUITY_OF_CARE"}),
    "R06": frozenset({"OPERATIONS", "CONTINUITY_OF_CARE"}),
    "R07": frozenset({"PAYMENT_COVERAGE"}),
    "R08": frozenset({"PROGRAM_ADMIN"}),
    "R09": frozenset({"OPERATIONS", "CONTINUITY_OF_CARE"}),
    "R10": frozenset({"INTEGRATION"}),
    "R11": frozenset({"SUPPORT", "OPERATIONS"}),
    "R12": frozenset({"SAFETY_REVIEW"}),
    "R13": frozenset({"OPERATIONS"}),
}

# Never allowed, whatever else is true. Checked first. Needs a different mechanism to ever open
# (for example R11 needs a just-in-time privileged grant, which is not built).
HARD_PROHIBITIONS: dict[str, frozenset[str]] = {
    "R08": frozenset(
        {
            "person",
            "booking",
            "referral",
            "workflow",
            "diagnostic",
            "attestation",
            "medication",
            "coverage",
            "evidence",
        }
    ),
    # Support sees metadata only (07-roles-access-matrix): never message content either.
    "R11": frozenset({"person", "diagnostic", "medication", "attestation", "coverage", "comms"}),
}

# role -> resource class -> (verbs, condition)
Rule = tuple[frozenset[str], str]


def _v(spec: str) -> frozenset[str]:
    return frozenset({"r": "read", "w": "write", "a": "attest"}[c] for c in spec)


RULES: dict[str, dict[str, Rule]] = {
    "R01": {
        "rights": (_v("rw"), "OWN"),
        "person": (_v("rw"), "OWN"),
        "booking": (_v("rw"), "OWN"),
        "referral": (_v("r"), "OWN"),
        "workflow": (_v("r"), "OWN"),
        "diagnostic": (_v("r"), "OWN"),
        "medication": (_v("r"), "OWN"),
        "coverage": (_v("rw"), "OWN"),
        "billing": (_v("r"), "OWN"),
        "comms": (_v("rw"), "OWN"),
        "evidence": (_v("r"), "OWN"),
        "catalog": (_v("r"), "ANY"),
    },
    "R02": {
        "person": (_v("r"), "PROXY"),
        "booking": (_v("rw"), "PROXY"),
        "referral": (_v("r"), "PROXY"),
        "workflow": (_v("r"), "PROXY"),
        "diagnostic": (_v("r"), "PROXY"),
        "medication": (_v("r"), "PROXY"),
        "coverage": (_v("r"), "PROXY"),
        "billing": (_v("r"), "PROXY"),
        "comms": (_v("rw"), "PROXY"),
        "catalog": (_v("r"), "ANY"),
    },
    "R03": {
        "person": (_v("r"), "REL"),
        "booking": (_v("r"), "REL"),
        "referral": (_v("rwa"), "REL"),
        "workflow": (_v("ra"), "REL"),
        "diagnostic": (_v("ra"), "REL"),
        "attestation": (_v("a"), "REL"),
        "medication": (_v("r"), "REL"),
        "comms": (_v("rw"), "REL"),
        "evidence": (_v("r"), "ORG"),
        "catalog": (_v("r"), "ANY"),
    },
    "R04": {
        "rights": (_v("rw"), "ORG"),
        "person": (_v("rw"), "ORG"),
        "booking": (_v("rw"), "ORG"),
        "referral": (_v("rw"), "ORG"),
        "workflow": (_v("rw"), "ORG"),
        "diagnostic": (_v("r"), "ORG"),
        "billing": (_v("rw"), "ORG"),
        "comms": (_v("rw"), "ORG"),
        "coverage": (_v("rw"), "ORG"),
        "catalog": (_v("rw"), "ORG"),
        "analytics": (_v("r"), "ORG"),
        "evidence": (_v("r"), "ORG"),
        "directory": (_v("r"), "ORG"),
    },
    "R05": {
        "staff": (_v("rw"), "ORG"),
        "diagnostic": (_v("rw"), "ORG"),
        "workflow": (_v("r"), "ORG"),
        "comms": (_v("rw"), "ORG"),
        "catalog": (_v("rw"), "ORG"),
        "partner": (_v("rw"), "ORG"),
    },
    "R06": {
        "staff": (_v("rw"), "ORG"),
        "medication": (_v("rw"), "ORG"),
        "billing": (_v("r"), "ORG"),
        "workflow": (_v("r"), "ORG"),
        "comms": (_v("rw"), "ORG"),
        "catalog": (_v("rw"), "ORG"),
        "partner": (_v("rw"), "ORG"),
    },
    "R07": {
        "staff": (_v("rw"), "ORG"),
        "coverage": (_v("rwa"), "ORG"),
        "billing": (_v("r"), "ORG"),
        "comms": (_v("rw"), "ORG"),
        "catalog": (_v("r"), "ANY"),
        "partner": (_v("rw"), "ORG"),
        "analytics": (_v("r"), "ORG"),
    },
    "R08": {
        "staff": (_v("rw"), "ORG"),
        "program": (_v("rw"), "ORG"),
        "billing": (_v("r"), "ORG"),
        "analytics": (_v("r"), "ORG"),
        "comms": (_v("r"), "ORG"),
        "catalog": (_v("r"), "ANY"),
    },
    "R09": {
        "staff": (_v("rw"), "ORG"),
        "person": (_v("r"), "REL"),
        "booking": (_v("r"), "ORG"),
        "referral": (_v("r"), "ORG"),
        "workflow": (_v("r"), "ORG"),
        "comms": (_v("rw"), "ORG"),
        "catalog": (_v("rw"), "ORG"),
    },
    "R10": {
        "integration": (_v("rw"), "ORG"),
        "staff": (_v("rw"), "ORG"),
        "partner": (_v("rw"), "ORG"),
        "catalog": (_v("r"), "ANY"),
        "evidence": (_v("r"), "ORG"),
        "ops": (_v("r"), "ORG"),
        "directory": (_v("r"), "ORG"),
    },
    "R11": {
        "workflow": (_v("rw"), "ORG"),
        "booking": (_v("r"), "ORG"),
        "ops": (_v("rw"), "ORG"),
        "catalog": (_v("rw"), "ORG"),
        "evidence": (_v("r"), "ORG"),
        "billing": (_v("rw"), "ORG"),
        "directory": (_v("r"), "ORG"),
        "analytics": (_v("r"), "ORG"),
        "platform": (_v("rwa"), "ORG"),  # add a customer (DEV-040); attest country packs
    },
    "R12": {
        "workflow": (_v("r"), "ORG"),
        "diagnostic": (_v("r"), "ORG"),
        "attestation": (_v("r"), "ORG"),
        "evidence": (_v("r"), "ORG"),
        "catalog": (_v("r"), "ANY"),
        "analytics": (_v("r"), "ORG"),
        "directory": (_v("r"), "ORG"),
        "platform": (_v("ra"), "ORG"),   # safety officers attest country packs (DEV-128)
    },
}

def _rules_digest() -> str:
    """A fingerprint of the rules in force, written into every access decision's audit row, so a
    reviewer can tell exactly which rules decided it (GHE-10 policy_digest)."""
    import hashlib
    import json

    def plain(value: object) -> object:
        if isinstance(value, (set, frozenset)):
            return sorted((plain(v) for v in value), key=repr)
        if isinstance(value, dict):
            return {str(k): plain(v) for k, v in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [plain(v) for v in value]
        return value

    body = plain({"version": POLICY_VERSION, "rules": RULES, "hard": HARD_PROHIBITIONS,
                  "purposes": ROLE_PURPOSES, "scopes": PROXY_SCOPE_OF,
                  "read_only": READ_ONLY_PURPOSES})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


# The organization administrator (R13, DEV-037) does everything clinic operations does, and may
# also manage the organization's own team. Hard prohibitions are NOT inherited from nowhere: R13
# has none, and R04's absence of patient-care rights carries over because the rules are copied.
RULES["R13"] = {**RULES["R04"], "staff": (_v("rw"), "ORG"), "integration": (_v("rw"), "ORG")}


def role_capabilities(role_code: str) -> list[dict[str, object]]:
    """What a role may do, from the same table the policy engine decides with: resource class, the
    verbs and the condition (OWN, ORG, REL, ANY). Used to tell a person what their account can
    reach (DEV-158)."""
    return [{"resource": res, "verbs": sorted(verbs), "scope": scope}
            for res, (verbs, scope) in sorted(RULES.get(role_code, {}).items())]


# Computed once, after every rule above is final (R13 included).
POLICY_DIGEST = _rules_digest()
