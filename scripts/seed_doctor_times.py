"""Give each demo organization a doctor with appointment times. DEVELOPMENT ONLY.

    Demo Clinic   venkydoctor@gmail.com   demo@123   (already exists)
    appolo        appolodoctor@gmail.com  demo@123   (created here)

Six 30-minute General Physician times are added for each doctor, three days from now, through the
real staff API as the organization's receptionist. A patient who books one and a receptionist who
confirms it makes the appointment appear in the doctor app (http://localhost:5176/).

    python scripts/seed_doctor_times.py
"""
import datetime as dt
import json
import urllib.error
import urllib.request

DEV = "http://127.0.0.1:8000/dev/api/"
API = "http://127.0.0.1:8000/api/v1/"
PASSWORD = "demo@123"
PLAN = (
    # organization, doctor login, receptionist login, create the doctor here?
    ("Demo Clinic", "venkydoctor@gmail.com", "reception@gmail.com", False),
    ("appolo", "appolodoctor@gmail.com", "reception@appolo.example.org", True),
)


def post(url, body, token=None):
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as err:
        return err.code, json.load(err)


def get(url, token=None):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


tenants = get(DEV + "state")["tenants"]
first = (dt.datetime.now() + dt.timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
for org, doctor, desk, create in PLAN:
    tenant = next((t["public_id"] for t in tenants if t["name"].lower() == org.lower()), None)
    if tenant is None:
        print(f'skipped "{org}": it does not exist')
        continue
    overview = get(DEV + "state?tenant=" + tenant)["overview"]
    actors = {a["subject"]: a["public_id"] for a in overview["actors"]}
    if doctor not in actors and create:
        site = overview["organizations"][0]["public_id"]
        actor = post(DEV + "actor", {"tenant": tenant, "actor_type": "clinician", "issuer": "local",
                                     "subject": doctor})[1]["public_id"]
        post(DEV + "assign-role", {"tenant": tenant, "actor": actor, "organization": site, "role_code": "R03"})
        post(DEV + "set-password", {"tenant": tenant, "actor": actor, "login": doctor, "password": PASSWORD})
        actors[doctor] = actor
        print(f"created doctor {doctor} / {PASSWORD} in {org}")
    if doctor not in actors:
        print(f"skipped {org}: {doctor} does not exist")
        continue
    token = post(API + "auth/login", {"organization": org, "login": desk, "password": PASSWORD})[1].get("token")
    if not token:
        print(f"skipped {org}: cannot sign in as {desk}")
        continue
    service = next(s for s in get(API + "services?all=1", token)["services"] if s["name"] == "General Physician")
    place = get(API + "locations", token)["locations"][0]["id"]
    status, body = post(API + f"services/{service['id']}/slots", {
        "location": place, "first_local": first.isoformat(), "count": 6, "provider": actors[doctor]}, token)
    print(f'{org}: {"added 6 times for " + doctor if status == 201 else "skipped (" + str(status) + ", probably already added)"}')
