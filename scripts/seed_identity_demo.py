"""Demo data for the Duplicates page. DEVELOPMENT ONLY. Needs the E2E clinic (scripts/seed_e2e.py).

    docker compose exec -T backend python manage.py shell < scripts/seed_identity_demo.py

Adds four patients to "E2E Clinic": two with the same name and birth date (a likely duplicate), and two
brothers born the same day (flagged as possible twins, so two reviewers are needed). Safe to run again.
"""
import json
import urllib.request

from foundation.cell_context import tenant_transaction
from modules.trust import api as trust

BASE = "http://127.0.0.1:8000"
TENANT, SITE = "VyR1pcPNoUzHaczCjHzanQ", "WoUvYlHF0FAuM2lLjqu-bQ"


def call(path, body, token=None):
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req) as res:
        return json.load(res)


token = call("/api/v1/auth/login", {"organization": "E2E Clinic", "login": "e2e.admin@example.org",
                                    "password": "E2e-Pass-2026!"})["token"]
PEOPLE = [
    ("Meera Nair", "1988-04-02", "+44 7700 900411"), ("Meera Nair", "1988-04-02", "+44 7700 900411"),
    ("Arjun Rao", "2001-09-17", "+44 7700 900522"), ("Kiran Rao", "2001-09-17", "+44 7700 900522"),
]
for name, birth, phone in PEOPLE:
    person = call("/dev/api/person", {"tenant": TENANT})["public_id"]
    with tenant_transaction(trust.context_for(TENANT)):
        trust.link_person_to_site(person, SITE, "booking")
    call(f"/api/v1/people/{person}/profile", {"name": name, "birth_date": birth, "phone": phone}, token)
    print("added", name, person)
