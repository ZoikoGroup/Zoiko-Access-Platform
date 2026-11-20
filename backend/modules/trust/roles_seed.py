"""The twelve product roles (docs/schema 07, GHE-15 §02). Rows, not code: edit freely."""
from __future__ import annotations

DEFAULT_ROLES: list[tuple[str, str, str, list[str]]] = [
    ("R01", "Patient", "Patient front door",
     ["No foreign chart access", "No unapproved record federation"]),
    ("R02", "Guardian or caregiver", "Proxy mode inside patient app",
     ["No blanket access", "Child or adult capacity rules depend on local law"]),
    ("R03", "Clinician or allied practitioner", "Professional workspace",
     ["Professional status alone is not permission for arbitrary patients"]),
    ("R04", "Clinic or hospital operations", "Provider or hospital console",
     ["No clinical judgments through the admin role"]),
    ("R05", "Lab or imaging operator", "Partner workspace",
     ["Do not infer reviewed findings from delivery"]),
    ("R06", "Pharmacy or dispensing partner", "Partner workspace",
     ["No substitution or dispensing by Zoiko"]),
    ("R07", "Payer, insurer or TPA", "Payer portal",
     ["Only contract and purpose authorized clinical data"]),
    ("R08", "Employer sponsor", "Employer portal",
     ["No individual health records, results or appointment details"]),
    ("R09", "Community or home-care partner", "Partner workspace",
     ["Safeguarding obligations and local qualification apply"]),
    ("R10", "Integration partner developer", "Developer console",
     ["No production PHI outside contracted scopes"]),
    ("R11", "Regional operations or support", "Operations console",
     ["No standing clinical access", "No global break-glass"]),
    ("R12", "Clinical safety officer", "Safety console",
     ["Does not replace provider clinical responsibility"]),
    # Our addition (DEV-037): the customer's own administrator. Manages THEIR organization's team.
    ("R13", "Organization administrator", "Provider or hospital console",
     ["No clinical judgments through the admin role", "Cannot grant Zoiko platform roles"]),
]
