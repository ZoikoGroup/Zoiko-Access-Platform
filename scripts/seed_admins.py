"""Give two demo organizations an ADMINISTRATOR login (role R13). DEVELOPMENT ONLY.

    Demo Clinic   clinicadmin@gmail.com    demo@123
    appolo        appoloadmin@gmail.com    demo@123

An administrator opens the staff console (http://localhost:5173/), does everything the receptionist
can, and also has a Team screen to invite doctors and receptionists and switch people off.

    python scripts/seed_admins.py
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000/dev/api/"
PASSWORD = "demo@123"
ADMINS = {"Demo Clinic": "clinicadmin@gmail.com", "appolo": "appoloadmin@gmail.com"}


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


tenants = json.load(urllib.request.urlopen(BASE + "state"))["tenants"]
for name, email in ADMINS.items():
    tenant = next((t["public_id"] for t in tenants if t["name"].lower() == name.lower()), None)
    if tenant is None:
        print(f'skipped "{name}": it does not exist (run its seed script first)')
        continue
    state = json.load(urllib.request.urlopen(BASE + "state?tenant=" + tenant))
    if any(a["subject"] == email for a in state["overview"]["actors"]):
        print(f"{email} already exists in {name}")
        continue
    site = state["overview"]["organizations"][0]["public_id"]
    actor = call("actor", tenant=tenant, actor_type="facility_user", issuer="local",
                 subject=email)["public_id"]
    call("assign-role", tenant=tenant, actor=actor, organization=site, role_code="R13")
    call("set-password", tenant=tenant, actor=actor, login=email, password=PASSWORD)
    print(f'created {email} / {PASSWORD}  administrator of "{name}"')
