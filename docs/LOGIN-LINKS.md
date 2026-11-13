# One-click sign-in links for every login (development only)

Open a link while the stack is running (`docker compose up -d` and the five apps on ports 5173 to 5177). Each link signs in once, through the real sign-in, and lands on that login's home screen. The address is cleaned straight away so the password does not stay in the address bar. These are **test accounts only**: never put a real person's password in a link. Make the accounts with `scripts/seed_e2e.py`, `scripts/seed_platform_tenant.py` and `scripts/seed_partners.py`.

| Role | Who | Open it signed in |
|---|---|---|
| R01 | Patient | [localhost:5174](http://localhost:5174/?dev_org=E2E+Clinic&dev_login=e2e.patient%40example.org&dev_pw=E2e-Pass-2026%21) |
| R03 | Doctor (clinician) | [localhost:5176](http://localhost:5176/?dev_org=E2E+Clinic&dev_login=e2e.doctor%40example.org&dev_pw=E2e-Pass-2026%21) |
| R04 | Receptionist (clinic operations) | [localhost:5173](http://localhost:5173/?dev_org=E2E+Clinic&dev_login=e2e.reception%40example.org&dev_pw=E2e-Pass-2026%21) |
| R13 | Clinic administrator | [localhost:5173](http://localhost:5173/?dev_org=E2E+Clinic&dev_login=e2e.admin%40example.org&dev_pw=E2e-Pass-2026%21) |
| R11 | Regional support (Zoiko) | [localhost:5175](http://localhost:5175/?dev_org=Zoiko+Platform&dev_login=vikram%40zoiko.example&dev_pw=Demo-Pass-2026%21) |
| R12 | Clinical safety officer (Zoiko) | [localhost:5175](http://localhost:5175/?dev_org=Zoiko+Platform&dev_login=iyer%40zoiko.example&dev_pw=Demo-Pass-2026%21) |
| R05 | Laboratory or imaging operator | [localhost:5177](http://localhost:5177/?dev_org=E2E+Lab&dev_login=e2e.lab%40example.org&dev_pw=E2e-Pass-2026%21) |
| R06 | Pharmacy partner | [localhost:5177](http://localhost:5177/?dev_org=E2E+Pharmacy&dev_login=e2e.pharmacy%40example.org&dev_pw=E2e-Pass-2026%21) |
| R09 | Home-care partner | [localhost:5177](http://localhost:5177/?dev_org=E2E+Homecare&dev_login=e2e.homecare%40example.org&dev_pw=E2e-Pass-2026%21) |
| R07 | Payer or insurer | [localhost:5177](http://localhost:5177/?dev_org=E2E+Payer&dev_login=e2e.payer%40example.org&dev_pw=E2e-Pass-2026%21) |
| R08 | Employer sponsor | [localhost:5177](http://localhost:5177/?dev_org=E2E+Employer&dev_login=e2e.employer%40example.org&dev_pw=E2e-Pass-2026%21) |
| R10 | Partner developer | [localhost:5177](http://localhost:5177/?dev_org=E2E+Developer&dev_login=e2e.developer%40example.org&dev_pw=E2e-Pass-2026%21) |

R02 (guardian or caregiver) signs in to the patient app like a patient; it needs a guardian login that a clinic has approved for a patient, so there is no ready-made test link. See docs/ROLES-AND-LOGINS.md.
