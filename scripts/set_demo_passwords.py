"""Give every login in one tenant the dev demo password (development only)."""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/dev/api/"
tenant = sys.argv[1]


def call(action, **body):
    req = urllib.request.Request(BASE + action, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


state = json.load(urllib.request.urlopen(BASE + "state?tenant=" + tenant))
for a in state["overview"]["actors"]:
    call("set-password", tenant=tenant, actor=a["public_id"], login=a["subject"], password="Demo-Pass-2026!")
    print("password set for", a["subject"], a["type"])
print("tenant name:", [t["name"] for t in state["tenants"] if t["public_id"] == tenant][0])
