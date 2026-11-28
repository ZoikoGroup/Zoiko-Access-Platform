"""Backup-and-restore rehearsal. DEVELOPMENT ONLY. Run from the project folder with Docker up:

    python scripts/recovery_drill.py

What it proves (the recovery evidence asked for in the Product Orientation document, section 12 item 08,
at the size of one development machine): a backup of the live database can be restored into a fresh
database, every table comes back with the same number of rows, each customer's audit chain still checks
out, and encrypted names still decrypt with our keys. It does NOT prove a regional failover, a restore on
different hardware, or a restore time at production size.

It writes docs/evidence/recovery-drill-<date>.md and removes the scratch database and the dump file.
It never prints passwords or settings. Exit code 0 only if every check passed.
"""
import datetime as dt
import json
import pathlib
import subprocess
import sys
import time

SCRATCH = "zoiko_drill"
DUMP = "/tmp/zoiko_drill.dump"
ROOT = pathlib.Path(__file__).resolve().parent.parent

COUNTS_SQL = (
    "select table_name, (xpath('/row/c/text()', query_to_xml("
    "format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text "
    "from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"
)

VERIFY_CODE = r'''
import json
from django.conf import settings
from django.db import connection
from foundation.cell_context import CellContext, tenant_transaction
from modules.evidence.api import verify_chain
from modules.trust import api as trust

out = {"database": connection.settings_dict["NAME"], "chains": [], "skipped": 0,
       "names_readable": 0, "names_stored": 0}
with connection.cursor() as c:
    c.execute("select distinct tenant_id from evidence_auditevent")
    tenants = [r[0] for r in c.fetchall()]
    c.execute("select count(*) from trust_actorname")
    out["names_stored"] = c.fetchone()[0]
for tenant in tenants:
    try:
        with tenant_transaction(CellContext(settings.ZOIKO_CELL_ID, tenant)):
            report = verify_chain()
            out["chains"].append({"ok": report.ok, "checked": report.checked, "problem": report.problem})
            out["names_readable"] += len(trust.display_names())
    except Exception as error:
        out["skipped"] += 1
print("RESULT " + json.dumps(out))
'''


def sh(args, input_text=None):
    done = subprocess.run(args, capture_output=True, text=True, input=input_text)
    return done.returncode, done.stdout, done.stderr


def in_db(command):
    """Run a shell command inside the database container (it knows its own user and database name)."""
    return sh(["docker", "compose", "exec", "-T", "db", "sh", "-c", command])


def counts(database_expr):
    code, out, err = in_db(f'psql -U "$POSTGRES_USER" -d {database_expr} -t -A -F "|" -c "{COUNTS_SQL}"')
    if code != 0:
        raise SystemExit(f"could not count rows in {database_expr}: {err.strip()[:200]}")
    rows = {}
    for line in out.strip().splitlines():
        table, _, number = line.partition("|")
        rows[table] = int(number)
    return rows


def run() -> int:
    started = dt.datetime.now(dt.timezone.utc)
    checks: list[tuple[str, bool, str]] = []
    timings: dict[str, float] = {}

    before = counts('"$POSTGRES_DB"')

    t = time.time()
    code, _, err = in_db(f'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" -f {DUMP}')
    timings["backup"] = time.time() - t
    if code != 0:
        raise SystemExit(f"backup failed: {err.strip()[:300]}")
    _, size_out, _ = in_db(f"wc -c < {DUMP}")
    size_bytes = int(size_out.strip() or 0)

    after = counts('"$POSTGRES_DB"')
    stable = before == after
    checks.append(("The live database did not change while the backup ran", stable,
                   "" if stable else "it changed: run the drill again when nothing is happening"))

    in_db(f'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS {SCRATCH}"')
    code, _, err = in_db(f'psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE {SCRATCH}"')
    if code != 0:
        raise SystemExit(f"could not create the scratch database: {err.strip()[:300]}")

    t = time.time()
    code, _, err = in_db(f'pg_restore -U "$POSTGRES_USER" -d {SCRATCH} --no-owner --exit-on-error {DUMP}')
    timings["restore"] = time.time() - t
    checks.append(("The backup restored into an empty database without errors", code == 0,
                   "" if code == 0 else err.strip()[:300]))

    restored = counts(SCRATCH) if code == 0 else {}
    differences = [f"{name}: {before.get(name)} before, {restored.get(name)} after"
                   for name in sorted(set(before) | set(restored)) if before.get(name) != restored.get(name)]
    checks.append((f"Every one of the {len(before)} tables came back with the same number of rows",
                   code == 0 and not differences, "; ".join(differences[:5])))

    verify = {}
    if code == 0:
        _, out, err = sh(["docker", "compose", "exec", "-T", "-e", f"POSTGRES_DB={SCRATCH}", "backend",
                          "python", "manage.py", "shell"], input_text=VERIFY_CODE)
        line = next((ln for ln in out.splitlines() if ln.startswith("RESULT ")), "")
        verify = json.loads(line[7:]) if line else {}
    chains = verify.get("chains", [])
    chains_ok = bool(chains) and all(c["ok"] for c in chains) and verify.get("skipped", 1) == 0
    checks.append((f"The audit chain of each of the {len(chains)} customers checks out in the restored copy "
                   f"({sum(c['checked'] for c in chains)} entries)", chains_ok,
                   "" if chains_ok else f"{verify!r}"[:300]))
    names_ok = bool(verify) and verify.get("names_readable") == verify.get("names_stored")
    checks.append((f"All {verify.get('names_stored', '?')} encrypted display names still decrypt with our keys",
                   names_ok, ""))

    in_db(f'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS {SCRATCH}"')
    in_db(f"rm -f {DUMP}")

    passed = all(ok for _, ok, _ in checks)
    lines = [f"# Recovery drill, {started:%Y-%m-%d %H:%M} UTC", "",
             f"**Result: {'PASSED' if passed else 'FAILED'}**", "",
             "A backup of the development database was restored into a scratch database and checked.", "",
             "| Check | Result |", "|---|---|"]
    for name, ok, detail in checks:
        lines.append(f"| {name} | {'passed' if ok else 'FAILED' + (' (' + detail + ')' if detail else '')} |")
    lines += ["", f"- Backup size: {size_bytes / 1024:.0f} KiB. Backup took {timings['backup']:.1f} s; "
              f"restore took {timings['restore']:.1f} s.",
              f"- Tables compared: {len(before)}. Rows in total: {sum(before.values())}.", "",
              "**What this does not prove:** a regional failover, a restore on different hardware, a restore "
              "time at production size, or that backups are taken on a schedule and kept somewhere safe. "
              "Those need the hosting decisions (backlog, production setup).", ""]
    folder = ROOT / "docs" / "evidence"
    folder.mkdir(exist_ok=True)
    target = folder / f"recovery-drill-{started:%Y-%m-%d}.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWritten to {target.relative_to(ROOT)}")
    return 0 if passed else 1


def main() -> int:
    try:
        return run()
    finally:                      # even when a step stops the drill, leave no scratch database or dump behind
        in_db(f'psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS {SCRATCH}"')
        in_db(f"rm -f {DUMP}")


if __name__ == "__main__":
    sys.exit(main())
