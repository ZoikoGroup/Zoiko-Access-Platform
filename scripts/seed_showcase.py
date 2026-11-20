"""Fill "Demo Clinic" with realistic people and activity so every screen has something to show.
DEVELOPMENT ONLY. Needs the stack running and the simple demo already created
(scripts/seed_simple_demo.py, scripts/seed_doctor_times.py).

    python scripts/seed_showcase.py

It adds, through the real APIs:
  patients   Ravi Menon, Priya Nair, Arjun Iyer, Fatima Khan   (password demo@123, organization Demo Clinic)
  doctors    Dr Mehta (dr.mehta@example.org), Dr Sharma (dr.sharma@example.org)
  activity   names and phone numbers, two insurance policies, confirmed and waiting appointments,
             a referral that was accepted and one waiting, a waiting-list entry, a family request.
Run it once: it stops if Ravi Menon's login already exists.
"""
import datetime as dt
import json
import urllib.error
import urllib.request
import uuid

DEV = "http://127.0.0.1:8000/dev/api/"
API = "http://127.0.0.1:8000/api/v1/"
ORG, PASSWORD = "Demo Clinic", "demo@123"

PATIENTS = [
    ("ravi.menon@example.org", "Ravi Menon", "1984-02-17", "+91 98450 11122"),
    ("priya.nair@example.org", "Priya Nair", "1991-09-03", "+91 98450 33344"),
    ("arjun.iyer@example.org", "Arjun Iyer", "1976-12-21", "+91 98450 55566"),
    ("fatima.khan@example.org", "Fatima Khan", "2001-06-30", "+91 98450 77788"),
]
DOCTORS = ["dr.mehta@example.org", "dr.sharma@example.org"]


def raw(url, body=None, token=None, method=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    head = {"Content-Type": "application/json", **(headers or {})}
    if token:
        head["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data, head, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as err:
        return err.code, json.load(err)


def dev(action, **body):
    return raw(DEV + action, body)[1]


def api(path, body=None, token=None, method=None, **headers):
    return raw(API + path, body, token, method, headers)


def login(who):
    status, body = api("auth/login", {"organization": ORG, "login": who, "password": PASSWORD})
    if status != 200:
        raise SystemExit(f"cannot sign in as {who}: run seed_simple_demo.py first")
    return body["token"]


state = raw(DEV + "state")[1]
tenant = next((t["public_id"] for t in state["tenants"] if t["name"] == ORG), None)
if tenant is None:
    raise SystemExit(f'"{ORG}" does not exist: run scripts/seed_simple_demo.py first')
overview = raw(DEV + "state?tenant=" + tenant)[1]["overview"]
actors = {a["subject"]: a["public_id"] for a in overview["actors"]}
if PATIENTS[0][0] in actors:
    raise SystemExit("The showcase data is already there.")
site = overview["organizations"][0]["public_id"]

# ---- people (the dev console is the only way to create logins without an invitation)
for email in DOCTORS:
    actor = dev("actor", tenant=tenant, actor_type="clinician", issuer="local", subject=email)["public_id"]
    dev("assign-role", tenant=tenant, actor=actor, organization=site, role_code="R03")
    dev("set-password", tenant=tenant, actor=actor, login=email, password=PASSWORD)
    actors[email] = actor
for email, *_ in PATIENTS:
    actor = dev("actor", tenant=tenant, actor_type="patient", issuer="local", subject=email)["public_id"]
    dev("assign-role", tenant=tenant, actor=actor, organization=site, role_code="R01")
    dev("set-password", tenant=tenant, actor=actor, login=email, password=PASSWORD)
    person = dev("person", tenant=tenant)["public_id"]
    dev("link-person", tenant=tenant, person=person, actor=actor)
print("created 2 doctors and 4 patients")

desk = login("reception@gmail.com")
tokens = {email: login(email) for email, *_ in PATIENTS}
docs = {email: login(email) for email in DOCTORS}

# ---- each patient enters their own details; two add insurance
for email, name, born, phone in PATIENTS:
    me = api("me", token=tokens[email])[1]["persons"][0]
    api(f"people/{me}/profile", {"name": name, "birth_date": born, "phone": phone}, tokens[email])
api("coverage", {"insurer": "Star Health", "plan": "Family Floater", "member_id": "STAR-20481-7731",
                 "valid_until": "2027-12-31"}, tokens["ravi.menon@example.org"])
api("coverage", {"insurer": "HDFC Ergo", "plan": "Optima", "member_id": "HE-559021",
                 "valid_until": "2027-06-30"}, tokens["priya.nair@example.org"])

# ---- times with the new doctors
services = {s["name"]: s["id"] for s in api("services?all=1", token=desk)[1]["services"]}
place = api("locations", token=desk)[1]["locations"][0]["id"]
first = (dt.datetime.now() + dt.timedelta(days=4)).replace(hour=10, minute=0, second=0, microsecond=0)
api(f"services/{services['General Physician']}/slots", {
    "location": place, "first_local": first.isoformat(), "count": 4,
    "provider": actors["dr.mehta@example.org"]}, desk)
api(f"services/{services['General Physician']}/slots", {
    "location": place, "first_local": (first + dt.timedelta(hours=3)).isoformat(), "count": 4,
    "provider": actors["dr.sharma@example.org"]}, desk)
api(f"services/{services['Cardiology']}/slots", {
    "location": place, "first_local": (first + dt.timedelta(days=1)).isoformat(), "count": 3}, desk)

# ---- bookings: some confirmed, two left waiting for the clinic
def free_gp(provider):
    slots = api(f"services/{services['General Physician']}/slots", token=desk)[1]["slots"]
    return [s["id"] for s in slots if s["provider"] and s["provider"]["name"] == provider]


def book(email, slot):
    status, body = raw(API + "bookings", {"slot": slot}, tokens[email], None,
                       {"Idempotency-Key": uuid.uuid4().hex})
    return body["booking"]["id"] if status == 201 else None


mehta, sharma = free_gp("dr.mehta@example.org"), free_gp("dr.sharma@example.org")
confirmed = []
for email, slot in (("ravi.menon@example.org", mehta[0]), ("priya.nair@example.org", mehta[1]),
                    ("arjun.iyer@example.org", sharma[0])):
    booking = book(email, slot)
    if booking:
        api(f"bookings/{booking}/confirm", token=desk)
        confirmed.append(booking)
book("fatima.khan@example.org", sharma[1])               # left waiting for the clinic
book("ravi.menon@example.org", sharma[2])                # a second one waiting
print("created appointments (3 confirmed, 2 waiting)")

# ---- referrals (a doctor can refer only patients they now treat)
def person_of(email):
    return api("me", token=tokens[email])[1]["persons"][0]


status, body = api("clinician/referrals", {
    "patient": person_of("ravi.menon@example.org"), "service": services["Cardiology"],
    "reason": "Palpitations for two weeks, family history of heart disease", "urgency": "urgent"},
    docs["dr.mehta@example.org"])
if status == 201:
    api(f"referrals/{body['referral']['id']}/decide", {"decision": "accepted"}, desk)
api("clinician/referrals", {
    "patient": person_of("arjun.iyer@example.org"), "service": services["Dermatology"],
    "reason": "Recurring rash on both forearms", "urgency": "routine"}, docs["dr.sharma@example.org"])

# ---- waiting list and a family request
api("waitlist", {"service": services["Dermatology"]}, tokens["priya.nair@example.org"])
api("family", {"relationship": "parent", "name": "Aarav Nair", "birth_date": "2019-04-11"},
    tokens["priya.nair@example.org"])
print("created referrals, a waiting-list entry and a family request")

print("""
Done. Sign in (organization Demo Clinic, password demo@123):
  Staff console  http://localhost:5173/   reception@gmail.com or clinicadmin@gmail.com
  Doctor app     http://localhost:5176/   dr.mehta@example.org or dr.sharma@example.org
  Patient app    http://localhost:5174/   ravi.menon@example.org, priya.nair@example.org,
                                          arjun.iyer@example.org, fatima.khan@example.org
""")
