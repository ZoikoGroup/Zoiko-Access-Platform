"""Create Zoiko's own platform tenant with two support logins and a safety login (development only).

Safe to run again: it adds whichever of the logins below are missing and leaves the others alone.
Two different support logins are needed to try the two-person approval of a customer suspension
(one asks, the other approves).

    vikram@zoiko.example   support (R11)
    priya@zoiko.example    support (R11), the second person
    iyer@zoiko.example     safety officer (R12): can look, cannot decide

Organization name at sign-in: Zoiko Platform. Password: see scripts/README.md.
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000/dev/api/"
PASSWORD = "Demo-Pass-2026!"
LOGINS = (("vikram@zoiko.example", "R11"), ("priya@zoiko.example", "R11"),
          ("iyer@zoiko.example", "R12"))


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


def get(path):
    with urllib.request.urlopen(BASE + path) as res:
        return json.load(res)


state = get("state")
existing = [t for t in state["tenants"] if t["type"] == "platform"]
if existing:
    tenant = existing[0]["public_id"]
    overview = get("state?tenant=" + tenant)["overview"]
    hq = overview["organizations"][0]["public_id"]
    have = {a["subject"] for a in overview["actors"]}
    print("platform tenant already exists:", existing[0]["name"])
else:
    tenant = call("tenant", name="Zoiko Platform", type="platform")["public_id"]
    hq = call("organization", tenant=tenant, name="Zoiko HQ")["public_id"]
    have = set()

for login, role in LOGINS:
    if login in have:
        print("already there:", login, role)
        continue
    actor = call("actor", tenant=tenant, actor_type="support", issuer="dev-idp", subject=login)["public_id"]
    call("assign-role", tenant=tenant, actor=actor, organization=hq, role_code=role)
    call("set-password", tenant=tenant, actor=actor, login=login, password=PASSWORD)
    print("created", login, role)
print("organization name: Zoiko Platform")
