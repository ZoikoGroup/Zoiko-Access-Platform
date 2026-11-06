"""A dedicated test clinic for the browser tests (frontend/e2e). DEVELOPMENT ONLY.

    python scripts/seed_e2e.py

Creates the organization "E2E Clinic" (separate from "Demo Clinic", so the robots never touch your
demo data) with three logins, one place and one service. Safe to run again: it says so and stops if the
clinic already exists. The robots add their own free times each run.

    e2e.admin@example.org     clinic administrator (R13)  -> staff console   http://localhost:5173/
    e2e.patient@example.org   patient (R01)               -> patient app      http://localhost:5174/
    e2e.doctor@example.org    doctor (R03)                -> doctor app       http://localhost:5176/

Password for all three: E2e-Pass-2026!
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000/dev/api/"
ORG = "E2E Clinic"
PASSWORD = "E2e-Pass-2026!"


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


state = json.load(urllib.request.urlopen(BASE + "state"))
if any(t["name"] == ORG for t in state["tenants"]):
    print(f'"{ORG}" already exists: nothing to do.')
    raise SystemExit

tenant = call("tenant", name=ORG, type="provider_org")["public_id"]
call("self-registration", tenant=tenant, enabled=True)
site = call("organization", tenant=tenant, name="E2E Clinic Main")["public_id"]


def login(email, kind, role):
    actor = call("actor", tenant=tenant, actor_type=kind, issuer="local", subject=email)["public_id"]
    call("assign-role", tenant=tenant, actor=actor, organization=site, role_code=role)
    call("set-password", tenant=tenant, actor=actor, login=email, password=PASSWORD)
    return actor


admin = login("e2e.admin@example.org", "facility_user", "R13")
doctor = login("e2e.doctor@example.org", "clinician", "R03")
patient = login("e2e.patient@example.org", "patient", "R01")

person = call("person", tenant=tenant)["public_id"]
call("link-person", tenant=tenant, person=person, actor=patient)
call("care-start", tenant=tenant, actor=doctor, person=person, basis="care_team")

place = call("location", tenant=tenant, actor=admin, organization=site, name="E2E Clinic, Main Block",
             time_zone="Asia/Kolkata", address="1 Test Road")["public_id"]
call("service", tenant=tenant, actor=admin, organization=site, name="E2E General Consult",
     category="general_physician", duration_minutes=30, description="Used by the browser tests")
print(f'Created "{ORG}" (place {place}). Logins above; password {PASSWORD}')
