"""Which changes to the published events would break a consumer still running the last release
(GHE-20 F-03, DEV-212).

`foundation/asyncapi.snapshot.json` changes with every edit to the event catalogue, so it shows a
change but cannot say whether the change is safe. `foundation/released_events.json` is frozen at a
release (`manage.py freeze_event_contract <release>`), and a test compares today's catalogue with
it using the rules below. A breaking change has to be published under a NEW name (`.v2`) while the
old one keeps being served, so an old worker never meets a payload it cannot read.

Breaking, for an event that exists in the frozen release:
* the event is gone;
* a field is gone, or a field that was always present becomes optional;
* a field is added (every schema says `additionalProperties: false`, so an old reader that checks
  the schema would refuse the new field);
* a field's type changes, or the event's subject (`x-aggregate`) changes.
Adding a whole new event is never breaking.
"""
from __future__ import annotations

from typing import Any


def _events(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    messages = (doc.get("components") or {}).get("messages") or {}
    return {m["name"]: m["payload"] for m in messages.values()}


def event_breaking_changes(released: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Plain-words list of what in `current` would break a consumer of `released`. Empty is safe."""
    old, new = _events(released), _events(current)
    problems: list[str] = []
    for name in sorted(old):
        if name not in new:
            problems.append(f"{name}: the event is no longer published")
            continue
        before, after = old[name], new[name]
        if before.get("x-aggregate") != after.get("x-aggregate"):
            problems.append(f"{name}: its subject changed")
        old_props, new_props = before.get("properties", {}), after.get("properties", {})
        for field in sorted(set(old_props) - set(new_props)):
            problems.append(f"{name}: field '{field}' was removed")
        for field in sorted(set(new_props) - set(old_props)):
            problems.append(
                f"{name}: field '{field}' was added (old readers refuse unknown fields)")
        for field in sorted(set(old_props) & set(new_props)):
            if old_props[field] != new_props[field]:
                problems.append(f"{name}: field '{field}' changed type")
        for field in sorted(set(before.get("required", [])) - set(after.get("required", []))):
            if field in new_props:
                problems.append(f"{name}: field '{field}' is no longer always present")
        if (before.get("additionalProperties") is False
                and after.get("additionalProperties") is not False):
            problems.append(f"{name}: it now allows fields nobody listed")
    return problems
