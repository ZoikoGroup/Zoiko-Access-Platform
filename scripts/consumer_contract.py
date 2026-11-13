"""Lists every API call each front-end app makes, and writes backend/api/consumer_contract.json
(GHE-13 T-03, DEV-168). A test then checks that the API still serves each one: adding to the API
passes, removing or changing a call an app uses fails.

    python scripts/consumer_contract.py          # rewrite the file
    python scripts/consumer_contract.py --check  # exit 1 if the file is out of date
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backend" / "api" / "consumer_contract.json"
APPS = ["patient", "clinician", "operations", "platform", "partners"]
# helper(<generic>)("path", { ... })   or   fetch("/api/v1/path", { ... })
CALL = re.compile(
    r"""(?:\b(?:v1|request)(?:<(?:[^()]|\([^()]*\))*?>)?\(\s*|\bfetch\(\s*[`"']/api/v1/)
        (?P<q>[`"'])(?P<path>[^`"']+)(?P=q)""",
    re.X)
METHOD = re.compile(r"method:\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']")


def _shape(path: str) -> str | None:
    path = path.split("?")[0].lstrip("/")
    if not path or "${" in path.split("/")[0] or path.startswith("${path}"):
        return None                       # built from a variable: not a fixed route
    return "/" + re.sub(r"\$\{[^}]*\}", "{x}", path)


def collect() -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    for app in APPS:
        calls: set[str] = set()
        for src in sorted((ROOT / "frontend" / app / "src").rglob("*.ts*")):
            text = src.read_text(encoding="utf-8")
            hits = list(CALL.finditer(text))
            for i, m in enumerate(hits):
                shaped = _shape(m.group("path"))
                if shaped is None:
                    continue
                end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
                method = METHOD.search(text[m.end():min(end, m.end() + 400)])
                calls.add(f"{method.group(1) if method else 'GET'} {shaped}")
        found[app] = calls
    return {app: sorted(calls) for app, calls in found.items()}


def main() -> int:
    text = json.dumps(collect(), indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        return 0 if OUT.read_text(encoding="utf-8") == text else 1
    OUT.write_text(text, encoding="utf-8")
    print({k: len(v) for k, v in json.loads(text).items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
