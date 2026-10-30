"""The access rules as data (docs/schema 07 "Data access by role"). Edit here, not in the engine.

Anything not listed is DENIED. Each rule says which verbs a role may use on a resource class
and what must also be true:

    ANY    nothing more (public catalog data)
    ORG    the actor holds that role at the organization named in the request
    REL    a verified care relationship with the patient exists  (not built yet: fails closed)
    OWN    the patient is the actor themselves                    (not built yet: fails closed)
    PROXY  a verified delegation from the patient exists          (not built yet: fails closed)
"""
from __future__ import annotations

POLICY_VERSION = "p1-2026-10-proposed"

VERBS = ("read", "write", "attest")

RESOURCE_CLASSES = (
    "person", "booking", "referral", "workflow", "diagnostic", "attestation", "medication",
    "coverage", "billing", "comms", "evidence", "catalog", "partner", "analytics", "ops",
    "directory", "platform", "staff",
)

PURPOSES = (
    "SELF_SERVICE", "DIRECT_CARE", "CONTINUITY_OF_CARE", "OPERATIONS", "PAYMENT_COVERAGE",
    "PROGRAM_ADMIN", "INTEGRATION", "SUPPORT", "SAFETY_REVIEW",
)

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
    "R08": frozenset({
        "person", "booking", "referral", "workflow", "diagnostic", "attestation",
        "medication", "coverage", "evidence",
    }),
    "R11": frozenset({"person", "diagnostic", "medication", "attestation", "coverage"}),
}

# role -> resource class -> (verbs, condition)
Rule = tuple[frozenset[str], str]


def _v(spec: str) -> frozenset[str]:
    return frozenset({"r": "read", "w": "write", "a": "attest"}[c] for c in spec)


RULES: dict[str, dict[str, Rule]] = {
    "R01": {
        "person": (_v("r"), "OWN"), "booking": (_v("rw"), "OWN"), "referral": (_v("r"), "OWN"),
        "workflow": (_v("r"), "OWN"), "diagnostic": (_v("r"), "OWN"),
        "medication": (_v("r"), "OWN"), "coverage": (_v("r"), "OWN"),
        "billing": (_v("r"), "OWN"), "comms": (_v("rw"), "OWN"), "evidence": (_v("r"), "OWN"),
        "catalog": (_v("r"), "ANY"),
    },
    "R02": {
        "person": (_v("r"), "PROXY"), "booking": (_v("rw"), "PROXY"),
        "referral": (_v("r"), "PROXY"), "workflow": (_v("r"), "PROXY"),
        "diagnostic": (_v("r"), "PROXY"), "medication": (_v("r"), "PROXY"),
        "coverage": (_v("r"), "PROXY"), "billing": (_v("r"), "PROXY"),
        "comms": (_v("rw"), "PROXY"), "catalog": (_v("r"), "ANY"),
    },
    "R03": {
        "person": (_v("r"), "REL"), "booking": (_v("r"), "REL"),
        "referral": (_v("rwa"), "REL"), "workflow": (_v("ra"), "REL"),
        "diagnostic": (_v("ra"), "REL"), "attestation": (_v("a"), "REL"),
        "medication": (_v("r"), "REL"), "comms": (_v("rw"), "REL"),
        "evidence": (_v("r"), "ORG"), "catalog": (_v("r"), "ANY"),
    },
    "R04": {
        "person": (_v("r"), "ORG"), "booking": (_v("rw"), "ORG"), "referral": (_v("rw"), "ORG"),
        "workflow": (_v("rw"), "ORG"), "diagnostic": (_v("r"), "ORG"),
        "billing": (_v("rw"), "ORG"), "comms": (_v("rw"), "ORG"),
        "catalog": (_v("rw"), "ORG"), "analytics": (_v("r"), "ORG"),
        "evidence": (_v("r"), "ORG"), "directory": (_v("r"), "ORG"),
    },
    "R05": {
        "person": (_v("r"), "ORG"), "diagnostic": (_v("rw"), "ORG"), "workflow": (_v("r"), "ORG"),
        "comms": (_v("rw"), "ORG"), "catalog": (_v("rw"), "ORG"), "partner": (_v("rw"), "ORG"),
    },
    "R06": {
        "person": (_v("r"), "ORG"), "medication": (_v("rw"), "ORG"), "billing": (_v("r"), "ORG"),
        "workflow": (_v("r"), "ORG"), "comms": (_v("rw"), "ORG"),
        "catalog": (_v("rw"), "ORG"), "partner": (_v("rw"), "ORG"),
    },
    "R07": {
        "person": (_v("r"), "ORG"), "coverage": (_v("rwa"), "ORG"), "billing": (_v("r"), "ORG"),
        "comms": (_v("rw"), "ORG"), "catalog": (_v("r"), "ANY"),
        "partner": (_v("rw"), "ORG"), "analytics": (_v("r"), "ORG"),
    },
    "R08": {
        "billing": (_v("r"), "ORG"), "analytics": (_v("r"), "ORG"), "comms": (_v("r"), "ORG"),
        "catalog": (_v("r"), "ANY"),
    },
    "R09": {
        "person": (_v("r"), "REL"), "booking": (_v("r"), "ORG"), "referral": (_v("r"), "ORG"),
        "workflow": (_v("r"), "ORG"), "comms": (_v("rw"), "ORG"), "catalog": (_v("rw"), "ORG"),
    },
    "R10": {
        "partner": (_v("rw"), "ORG"), "catalog": (_v("r"), "ANY"),
        "evidence": (_v("r"), "ORG"), "ops": (_v("r"), "ORG"), "directory": (_v("r"), "ORG"),
    },
    "R11": {
        "workflow": (_v("rw"), "ORG"), "booking": (_v("r"), "ORG"), "ops": (_v("rw"), "ORG"),
        "catalog": (_v("rw"), "ORG"), "evidence": (_v("r"), "ORG"),
        "billing": (_v("rw"), "ORG"), "comms": (_v("r"), "ORG"),
        "directory": (_v("r"), "ORG"), "analytics": (_v("r"), "ORG"),
        "platform": (_v("r"), "ORG"),
    },
    "R12": {
        "workflow": (_v("r"), "ORG"), "diagnostic": (_v("r"), "ORG"),
        "attestation": (_v("r"), "ORG"), "evidence": (_v("r"), "ORG"),
        "catalog": (_v("r"), "ANY"), "analytics": (_v("r"), "ORG"), "directory": (_v("r"), "ORG"),
        "platform": (_v("r"), "ORG"),
    },
}

# The organization administrator (R13, DEV-037) does everything clinic operations does, and may
# also manage the organization's own team. Hard prohibitions are NOT inherited from nowhere: R13
# has none, and R04's absence of patient-care rights carries over because the rules are copied.
RULES["R13"] = {**RULES["R04"], "staff": (_v("rw"), "ORG")}
