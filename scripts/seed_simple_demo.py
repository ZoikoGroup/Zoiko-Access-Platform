"""A small, easy-to-remember demo: organization "Demo Clinic" with three logins.

    venkydoctor@gmail.com   doctor        (clinician, R03)
    reception@gmail.com     receptionist  (clinic operations, R04)  -> staff console
    patient@gmail.com       patient       (R01)                     -> patient app

All use the password  demo@123   (8 characters: allowed only because the development Docker setup
sets PASSWORD_MIN_LENGTH=8; production keeps 10). DEVELOPMENT ONLY.

    python scripts/seed_simple_demo.py
"""
import datetime as dt
import json
import urllib.request

BASE = "http://127.0.0.1:8000/dev/api/"
ORG = "Demo Clinic"
PASSWORD = "demo@123"


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


state = json.load(urllib.request.urlopen(BASE + "state"))
if any(t["name"] == ORG for t in state["tenants"]):
    raise SystemExit(f'"{ORG}" already exists. Sign in with the logins in this file\'s header.')

tenant = call("tenant", name=ORG, type="provider_org")["public_id"]
call("self-registration", tenant=tenant, enabled=True)
site = call("organization", tenant=tenant, name="Demo Clinic Main")["public_id"]


def login(email, kind, role):
    actor = call("actor", tenant=tenant, actor_type=kind, issuer="local", subject=email)["public_id"]
    call("assign-role", tenant=tenant, actor=actor, organization=site, role_code=role)
    call("set-password", tenant=tenant, actor=actor, login=email, password=PASSWORD)
    return actor


doctor = login("venkydoctor@gmail.com", "clinician", "R03")
reception = login("reception@gmail.com", "facility_user", "R04")
patient = login("patient@gmail.com", "patient", "R01")

person = call("person", tenant=tenant)["public_id"]
call("link-person", tenant=tenant, person=person, actor=patient)
call("care-start", tenant=tenant, actor=doctor, person=person, basis="care_team")

place = call("location", tenant=tenant, actor=reception, organization=site, name="Demo Clinic, Main Block",
             time_zone="Asia/Kolkata", address="1 Demo Road")["public_id"]
first = (dt.datetime.now() + dt.timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
for name, category, minutes, description in (
    ("General Physician", "general_physician", 30, "Consult for everyday health concerns"),
    ("Dermatology", "dermatology", 20, "Skin, hair and nail care"),
    ("Blood test (CBC)", "diagnostics", 15, "Lab tests and scans"),
):
    service = call("service", tenant=tenant, actor=reception, organization=site, name=name,
                   category=category, duration_minutes=minutes, description=description)["public_id"]
    call("slots", tenant=tenant, actor=reception, service=service, location=place,
         first_local=first.isoformat(), count=8)

print(f'Created organization "{ORG}".')
print("  venkydoctor@gmail.com  / demo@123   doctor (no app yet)")
print("  reception@gmail.com    / demo@123   staff console  http://localhost:5173/")
print("  patient@gmail.com      / demo@123   patient app    http://localhost:5174/")
