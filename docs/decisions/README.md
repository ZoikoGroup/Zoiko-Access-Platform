# Decisions and deviations from the specification

The spec documents (GHE-01..23, R3.0 master) are the source of truth. When we build something the
documents do not say, or say inconsistently, or say in a way that does not work technically, we record it
here. Nothing is changed silently.

Files:
- [REGISTER.md](REGISTER.md): every gap/deviation and what we did about it
- [TEMPLATE.md](TEMPLATE.md): copy this for each new entry

## Kinds of entry

| Kind | Meaning | Who can close it |
|---|---|---|
| GAP | The docs are silent (no type, no enum, no retention rule) | Engineering. Mark the result INFERRED. |
| MERGE | Same concept appears under several names | Engineering |
| CONFLICT | Two docs disagree | Engineering proposes; the owner of the topic confirms |
| FIX | The docs' idea would not work technically | Engineering, but tell the lead |
| ADD | Engineering plumbing the docs do not mention (outbox, tests, tooling) | Engineering |
| ESCALATE | Touches a non-negotiable or an open business/legal/clinical decision | NOT engineering. Needs the named authority. |

## Never decided by engineering alone (always ESCALATE)

No global patient database or cross-cell links; source systems stay authoritative; a delivery receipt never
counts as clinical review; employer and payer data limits; AI/device features off by default; Zoiko's legal
operating role; retention periods; clinical safety classification; first market, buyer and journey.

## Status values

`PROPOSED` (we suggest it), `ACCEPTED` (owner confirmed), `OPEN` (waiting for an owner), `SUPERSEDED`.
Until an entry is ACCEPTED, build against it only behind an interface so it can be reversed.
