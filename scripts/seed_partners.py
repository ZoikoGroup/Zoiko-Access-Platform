"""Test customers for the partner, payer and employer portal. DEVELOPMENT ONLY.

    python scripts/seed_partners.py          (set ZOIKO_DEV_API if the backend is not on port 8000)

One customer per role, each with one login, so every kind of account can be tried in the partner app
(http://localhost:5177/). Safe to run again: a customer that exists is skipped. Password for all:
E2e-Pass-2026!

    E2E Lab        e2e.lab@example.org         R05  lab or imaging operator
    E2E Pharmacy   e2e.pharmacy@example.org    R06  pharmacy partner
    E2E Homecare   e2e.homecare@example.org    R09  home-care partner
    E2E Payer      e2e.payer@example.org       R07  payer
    E2E Employer   e2e.employer@example.org    R08  employer sponsor
    E2E Developer  e2e.developer@example.org   R10  partner developer
"""
import json
import os
import urllib.request

BASE = os.environ.get("ZOIKO_DEV_API", "http://127.0.0.1:8000/dev/api/")
PASSWORD = "E2e-Pass-2026!"
CUSTOMERS = [  # name, tenant type, login kind, role, login
    ("E2E Lab", "partner", "facility_user", "R05", "e2e.lab@example.org"),
    ("E2E Pharmacy", "partner", "facility_user", "R06", "e2e.pharmacy@example.org"),
    ("E2E Homecare", "partner", "facility_user", "R09", "e2e.homecare@example.org"),
    ("E2E Payer", "payer", "payer_user", "R07", "e2e.payer@example.org"),
    ("E2E Employer", "employer", "employer_user", "R08", "e2e.employer@example.org"),
    ("E2E Developer", "partner", "facility_user", "R10", "e2e.developer@example.org"),
]


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


state = json.load(urllib.request.urlopen(BASE + "state"))
have = {t["name"] for t in state["tenants"]}
for name, kind, login_kind, role, email in CUSTOMERS:
    if name in have:
        print(f'"{name}" already exists: skipped.')
        continue
    tenant = call("tenant", name=name, type=kind)["public_id"]
    site = call("organization", tenant=tenant, name=f"{name} Main")["public_id"]
    actor = call("actor", tenant=tenant, actor_type=login_kind, issuer="local", subject=email)["public_id"]
    call("assign-role", tenant=tenant, actor=actor, organization=site, role_code=role)
    call("set-password", tenant=tenant, actor=actor, login=email, password=PASSWORD)
    print(f'Created "{name}": {email} ({role}), password {PASSWORD}')
