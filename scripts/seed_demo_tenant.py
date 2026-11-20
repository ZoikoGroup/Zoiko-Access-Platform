"""Seed one demo tenant through the real dev API and print its public id."""
import datetime as dt
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/dev/api/"
name = sys.argv[1] if len(sys.argv) > 1 else "Demo Clinics"


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


t = call("tenant", name=name, type="provider_org")["public_id"]
north = call("organization", tenant=t, name="North Clinic")["public_id"]
call("organization", tenant=t, name="South Clinic")
ids = {}
for subject, kind, role in [
    ("ops@north.example", "facility_user", "R04"), ("hr@employer.example", "employer_user", "R08"),
    ("dr.rao@north.example", "clinician", "R03"), ("support@zoiko.example", "support", "R11"),
    ("asha.patient@example.org", "patient", "R01"), ("parent@example.org", "proxy", "R02"),
]:
    a = call("actor", tenant=t, actor_type=kind, issuer="dev-idp", subject=subject)["public_id"]
    call("assign-role", tenant=t, actor=a, organization=north, role_code=role)
    ids[role] = a
    call("set-password", tenant=t, actor=a, login=subject, password="Demo-Pass-2026!")
mine = call("person", tenant=t)["public_id"]
child = call("person", tenant=t)["public_id"]
call("person", tenant=t)
call("link-person", tenant=t, person=mine, actor=ids["R01"])
call("care-start", tenant=t, actor=ids["R03"], person=mine, basis="referral")
g = call("proxy-propose", tenant=t, delegate=ids["R02"], person=child, relationship_type="parent")["public_id"]
call("proxy-approve", tenant=t, grant=g, approver=ids["R04"])

loc = call("location", tenant=t, actor=ids["R04"], organization=north, name="North Clinic, Main Block",
           time_zone="Asia/Kolkata", address="12 Residency Road, Bengaluru")["public_id"]
first = (dt.datetime.now() + dt.timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
for name, cat, mins, mode, desc in [
    ("General Physician", "general_physician", 30, "in_person", "Consult for everyday health concerns"),
    ("Dermatology", "dermatology", 20, "in_person", "Skin, hair and nail care"),
    ("Orthopedics", "orthopedics", 30, "in_person", "Bone and joint care"),
    ("Blood test (CBC)", "diagnostics", 15, "in_person", "Lab tests and scans"),
    ("Physiotherapy", "physiotherapy", 45, "in_person", "Recovery and wellness"),
    ("Video follow-up", "general_physician", 20, "video", "Short follow-up from home"),
]:
    svc = call("service", tenant=t, actor=ids["R04"], organization=north, name=name, category=cat,
               duration_minutes=mins, mode=mode, description=desc)["public_id"]
    call("slots", tenant=t, actor=ids["R04"], service=svc, location=loc, first_local=first.isoformat(), count=8)

checks = [
    (ids["R04"], "write:booking", "OPERATIONS", north, ""), (ids["R08"], "read:person", "PROGRAM_ADMIN", north, mine),
    (ids["R08"], "read:billing", "PROGRAM_ADMIN", north, ""), (ids["R03"], "read:person", "DIRECT_CARE", "", mine),
    (ids["R03"], "read:person", "DIRECT_CARE", "", child), (ids["R01"], "read:person", "SELF_SERVICE", "", mine),
    (ids["R02"], "read:person", "SELF_SERVICE", "", child), (ids["R11"], "read:person", "SUPPORT", north, mine),
]
for actor, op, purpose, org, subject in checks:
    call("decide", tenant=t, actor=actor, operation=op, purpose=purpose, organization=org, subject=subject)
print(t)
