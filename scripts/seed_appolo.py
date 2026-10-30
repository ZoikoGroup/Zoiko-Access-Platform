"""Organization "appolo" for manual testing, with the logins below. DEVELOPMENT ONLY.

    venkyvenky40844@gmail.com    patient       -> patient app  http://localhost:5174/
    reception@appolo.example.org receptionist  -> staff console http://localhost:5173/

Both use the password  demo@123  (allowed only because the development Docker setup sets
PASSWORD_MIN_LENGTH=8). The organization name is not case sensitive: appolo / Appolo / APPOLO.

    python scripts/seed_appolo.py
"""
import datetime as dt
import json
import urllib.request

BASE = "http://127.0.0.1:8000/dev/api/"
ORG = "appolo"
PASSWORD = "demo@123"
PATIENT = "venkyvenky40844@gmail.com"
RECEPTION = "reception@appolo.example.org"


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


state = json.load(urllib.request.urlopen(BASE + "state"))
if any(t["name"].lower() == ORG for t in state["tenants"]):
    raise SystemExit(f'"{ORG}" already exists.')

tenant = call("tenant", name=ORG, type="provider_org")["public_id"]
call("self-registration", tenant=tenant, enabled=True)
site = call("organization", tenant=tenant, name="appolo Main")["public_id"]


def login(email, kind, role):
    actor = call("actor", tenant=tenant, actor_type=kind, issuer="local", subject=email)["public_id"]
    call("assign-role", tenant=tenant, actor=actor, organization=site, role_code=role)
    call("set-password", tenant=tenant, actor=actor, login=email, password=PASSWORD)
    return actor


reception = login(RECEPTION, "facility_user", "R04")
patient = login(PATIENT, "patient", "R01")
person = call("person", tenant=tenant)["public_id"]
call("link-person", tenant=tenant, person=person, actor=patient)

place = call("location", tenant=tenant, actor=reception, organization=site, name="appolo, Main Block",
             time_zone="Asia/Kolkata", address="1 Hospital Road")["public_id"]
first = (dt.datetime.now() + dt.timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
for name, category, minutes, description in (
    ("General Physician", "general_physician", 30, "Consult for everyday health concerns"),
    ("Cardiology", "cardiology", 30, "Heart and blood pressure care"),
    ("Dermatology", "dermatology", 20, "Skin, hair and nail care"),
    ("Blood test (CBC)", "diagnostics", 15, "Lab tests and scans"),
):
    service = call("service", tenant=tenant, actor=reception, organization=site, name=name,
                   category=category, duration_minutes=minutes, description=description)["public_id"]
    call("slots", tenant=tenant, actor=reception, service=service, location=place,
         first_local=first.isoformat(), count=8)

print(f'Created organization "{ORG}".')
print(f"  {PATIENT} / {PASSWORD}   patient")
print(f"  {RECEPTION} / {PASSWORD}   receptionist")
