"""Every call the five front-end apps make is still served (GHE-13 T-03, DEV-168).

api/consumer_contract.json lists the calls each app makes (made by scripts/consumer_contract.py
from the apps' own source). Adding routes passes; removing a route or method an app uses fails."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from api import openapi

CONTRACT = Path(__file__).resolve().parent.parent / "api" / "consumer_contract.json"
ROOT = CONTRACT.parent.parent.parent


def _served() -> set[str]:
    return {f"{m.upper()} {p}" for p, item in openapi.build_spec()["paths"].items() for m in item}


def _is_served(call: str, served: set[str]) -> bool:
    """A route's {name} part matches any one segment the app sends (a fixed word or an id), and a
    {x} the app sends (an id, or a step word chosen in code) matches any one segment of a route."""
    method, path = call.split(" ", 1)
    parts = path.split("/")
    for route in served:
        m, r = route.split(" ", 1)
        rp = r.split("/")
        if (
            m == method
            and len(rp) == len(parts)
            and all(
                x == y or x.startswith("{") or y == "{x}" for x, y in zip(rp, parts, strict=True)
            )
        ):
            return True
    return False


def _missing(contract: dict[str, list[str]], served: set[str]) -> list[str]:
    return [
        f"{app}: {call}"
        for app, calls in contract.items()
        for call in calls
        if not _is_served(call, served)
    ]


def test_every_call_an_app_makes_is_still_served():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert set(contract) == {"patient", "clinician", "operations", "platform", "partners"}
    assert sum(len(v) for v in contract.values()) > 120
    assert _missing(contract, _served()) == []


def test_adding_a_route_passes_and_removing_one_is_caught():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    served = _served()
    assert _missing(contract, served | {"GET /brand-new-route"}) == []
    call = next(c for cs in contract.values() for c in cs)
    hit = [f"{a}: {c}" for a, cs in contract.items() for c in cs if c == call]  # every app using it
    assert _missing(contract, served - {call}) == hit
    method, path = call.split(" ", 1)
    other = "DELETE" if method != "DELETE" else "PUT"
    assert _missing(contract, (served - {call}) | {f"{other} {path}"}) == hit


def test_the_contract_file_matches_the_apps_source():
    script = ROOT / "scripts" / "consumer_contract.py"
    if not script.exists() or not (ROOT / "frontend" / "patient" / "src").exists():
        pytest.skip("the front-end source is not visible from here")
    result = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(script), "--check"], capture_output=True
    )
    assert result.returncode == 0, "Refresh it: python scripts/consumer_contract.py"
