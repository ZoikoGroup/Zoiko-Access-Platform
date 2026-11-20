"""Draft the United Kingdom country pack (development; the first market chosen in DEV-195).

It creates a DRAFT pack only. A draft switches nothing on: it must be checked, sent for review, and
approved by a legal reviewer and a clinical reviewer (two different people, neither its author)
before it counts (DEV-128). The evidence is left PENDING on purpose: nobody has provided the legal,
privacy (DPIA) or cyber evidence for the UK yet, and this script must not pretend they have.

Settings proposed for the reviewers to confirm:
  emergency number   999 (the UK emergency number; 112 also works in the UK)
  data requests      30 days (UK GDPR: one month, which can be extended in some cases)
  language           en-GB
  time zone          Europe/London
  capabilities       provider directory, booking, messaging, referrals, waiting list

Safe to run again: if a pack of that name exists, it says so and changes nothing.

    python scripts/seed_uk_pack.py      (support login vikram@zoiko.example must exist:
                                          python scripts/seed_platform_tenant.py)
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.request

API = os.environ.get("ZOIKO_API", "http://127.0.0.1:8000/api/v1/")
ORG, LOGIN, PASSWORD = "Zoiko Platform", "vikram@zoiko.example", "Demo-Pass-2026!"
PACK = "gb-care-navigation"


def call(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data, headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req) as res:
        return json.load(res)


def main() -> None:
    token = call("auth/login", {"organization": ORG, "login": LOGIN, "password": PASSWORD})["token"]
    existing = [p for p in call("platform/packs", token=token)["packs"] if p["pack_ref"] == PACK]
    if existing:
        print(f'"{PACK}" already exists (revision {existing[0]["revision"]}, '
              f'{existing[0]["status"]}): nothing changed.')
        return
    manifest = {
        "settings": {"data_request_days": 30, "emergency_number": "999",
                     "default_language": "en-GB", "time_zone": "Europe/London"},
        "capabilities": {"provider_directory": True, "booking": True, "messaging": True,
                         "referrals": True, "waiting_list": True},
        "evidence": {"legal": "PENDING", "dpia": "PENDING", "cyber": "PENDING",
                     "clinical": "PENDING"},
        "validity": {"expires_on": (dt.date.today() + dt.timedelta(days=365)).isoformat()},
    }
    try:
        made = call("platform/packs", {"pack_ref": PACK, "country": "GB",
                                       "service": "care_navigation", "manifest": manifest}, token)
    except urllib.error.HTTPError as err:
        raise SystemExit(f"could not create the pack: HTTP {err.code}") from None
    pack = made["pack"]
    print(f'Drafted "{PACK}" for GB (revision {pack["revision"]}, status {pack["status"]}).')
    print("Still missing before it can be signed:", ", ".join(pack.get("missing") or []) or "none")


if __name__ == "__main__":
    main()
