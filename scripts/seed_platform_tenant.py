"""Create Zoiko's own platform tenant with a support and a safety login (development only)."""
import json
import urllib.request

BASE = "http://127.0.0.1:8000/dev/api/"
PASSWORD = "Demo-Pass-2026!"


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


state = json.load(urllib.request.urlopen(BASE + "state"))
existing = [t for t in state["tenants"] if t["type"] == "platform"]
if existing:
    print("platform tenant already exists:", existing[0]["name"])
    raise SystemExit

tenant = call("tenant", name="Zoiko Platform", type="platform")["public_id"]
hq = call("organization", tenant=tenant, name="Zoiko HQ")["public_id"]
for login, role in (("vikram@zoiko.example", "R11"), ("iyer@zoiko.example", "R12")):
    actor = call("actor", tenant=tenant, actor_type="support", issuer="dev-idp", subject=login)["public_id"]
    call("assign-role", tenant=tenant, actor=actor, organization=hq, role_code=role)
    call("set-password", tenant=tenant, actor=actor, login=login, password=PASSWORD)
    print("created", login, role)
print("organization name: Zoiko Platform")
