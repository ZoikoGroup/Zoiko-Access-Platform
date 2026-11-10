"""One page with every local link, and one-click sign-in for the demo accounts. DEVELOPMENT ONLY.

    python scripts/dev_links.py            # writes dev-links.html in the project folder and opens it
    python scripts/dev_links.py --no-open  # only writes it

The "Open signed in" buttons use the apps' development-only sign-in (frontend/shared/src/devLogin.ts): it is
removed from production builds. The passwords are the DEMO ones from docs/RUNNING-LOCALLY.md. The file is
ignored by git. Never share it and never put a real person's password in it.
"""
import html
import pathlib
import sys
import urllib.parse
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent.parent

APPS = {
    "staff": ("Staff console (clinics)", "http://localhost:5173"),
    "patient": ("Patient app", "http://localhost:5174"),
    "platform": ("Platform app (Zoiko staff)", "http://localhost:5175"),
    "doctor": ("Doctor app", "http://localhost:5176"),
}

# (app, label, organization, login, password)
ACCOUNTS = [
    ("staff", "Clinic administrator", "Demo Clinic", "clinicadmin@gmail.com", "demo@123"),
    ("staff", "Receptionist", "Demo Clinic", "reception@gmail.com", "demo@123"),
    ("patient", "Patient", "Demo Clinic", "patient@gmail.com", "demo@123"),
    ("doctor", "Doctor", "Demo Clinic", "venkydoctor@gmail.com", "demo@123"),
    ("platform", "Support (asks)", "Zoiko Platform", "vikram@zoiko.example", "Demo-Pass-2026!"),
    ("platform", "Support (approves)", "Zoiko Platform", "priya@zoiko.example", "Demo-Pass-2026!"),
    ("platform", "Safety officer (assesses hazards)", "Zoiko Platform", "iyer@zoiko.example", "Demo-Pass-2026!"),
    ("platform", "Second safety officer (reviews hazards)", "Zoiko Platform", "rao@zoiko.example", "Demo-Pass-2026!"),
    ("staff", "E2E clinic administrator (for the robots)", "E2E Clinic", "e2e.admin@example.org", "E2e-Pass-2026!"),
    ("patient", "E2E patient (for the robots)", "E2E Clinic", "e2e.patient@example.org", "E2e-Pass-2026!"),
    ("doctor", "E2E doctor (for the robots)", "E2E Clinic", "e2e.doctor@example.org", "E2e-Pass-2026!"),
]

HELPERS = [
    ("Mail inbox: every email the system sends", "http://127.0.0.1:8025"),
    ("Developer console: create customers, logins and test data", "http://127.0.0.1:8000/"),
    ("API health (should say ready)", "http://127.0.0.1:8000/readyz"),
    ("Raw test data as text (customers and logins that exist)", "http://127.0.0.1:8000/dev/api/state"),
]


def direct(app: str, org: str, login: str, password: str) -> str:
    query = urllib.parse.urlencode({"dev_org": org, "dev_login": login, "dev_pw": password})
    return f"{APPS[app][1]}/?{query}"


def link(url: str, text: str, cls: str = "") -> str:
    return f'<a class="{cls}" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(text)}</a>'


def build() -> str:
    app_cards = []
    for key, (name, url) in APPS.items():
        rows = "".join(
            f'<li>{link(direct(a, o, l, p), "Open signed in")} <span>{html.escape(label)}'
            f"<small>{html.escape(l)}</small></span></li>"
            for a, label, o, l, p in ACCOUNTS if a == key)
        app_cards.append(
            f'<section class="card"><h2><i class="dot" data-url="{html.escape(url)}"></i>{html.escape(name)}</h2>'
            f'<p>{link(url, url, "plain")} (sign in by hand)</p><ul>{rows}</ul></section>')
    helpers = "".join(
        f'<li><i class="dot" data-url="{html.escape(u)}"></i>{link(u, label)}<small>{html.escape(u)}</small></li>'
        for label, u in HELPERS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zoiko Access Local Links</title>
<style>
:root{{--bg:#f1f5fb;--card:#fff;--ink:#14213d;--muted:#52617c;--line:#e3e9f3;--brand:#1f5fe0;--ok:#138a4d;--bad:#c0352b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0c1220;--card:#141c2f;--ink:#e8edf8;--muted:#a9b8d6;--line:#25304a;--brand:#8fb1ff}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 48px}}
h1{{margin:0 0 4px;font-size:26px}} p.lead{{margin:0 0 20px;color:var(--muted)}}
.grid{{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}}
.card h2{{margin:0 0 6px;font-size:17px;display:flex;align-items:center;gap:8px}}
ul{{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:10px}}
li{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}} li span{{display:grid}} small{{color:var(--muted);display:block}}
a{{color:var(--brand);font-weight:600}} a:not(.plain){{text-decoration:none}}
li a:first-child:not(.plain){{background:var(--brand);color:#fff;padding:6px 12px;border-radius:9px}}
@media (prefers-color-scheme:dark){{li a:first-child:not(.plain){{color:#0c1220}}}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;background:#999;flex:none}}
.dot.up{{background:var(--ok)}} .dot.down{{background:var(--bad)}}
.warn{{margin-top:24px;padding:12px 14px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--muted);font-size:13px}}
</style></head><body><main>
<h1>Zoiko Access: local links</h1>
<p class="lead">Green dot: running. Red dot: not running (start it with docs/RUNNING-LOCALLY.md). "Open signed in" signs in as that demo account.</p>
<div class="grid">{"".join(app_cards)}
<section class="card"><h2>Helpers</h2><ul>{helpers}</ul></section></div>
<p class="warn">Development only. These buttons put DEMO passwords in the address for one second (the apps remove them at once) and only work while an app runs through the Vite dev server. Do not share this file, do not add a real person's login, and do not open it on a shared computer. It is ignored by git.</p>
<script>
document.querySelectorAll('.dot[data-url]').forEach(function(dot){{
  fetch(dot.dataset.url,{{mode:'no-cors',cache:'no-store'}}).then(function(){{dot.classList.add('up')}}).catch(function(){{dot.classList.add('down')}});
}});
</script></main></body></html>"""


def main() -> None:
    target = ROOT / "dev-links.html"
    target.write_text(build(), encoding="utf-8")
    print(f"Written to {target}")
    if "--no-open" not in sys.argv:
        webbrowser.open(target.as_uri())


if __name__ == "__main__":
    main()
