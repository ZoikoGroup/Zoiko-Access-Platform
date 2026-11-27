# Runbooks

What to do when something is wrong with the running service (DEV-388, GHE-18 and GHE-20). Each
alert on the platform console's **Service levels** page names the runbook to open.

Written for: the on-call person at Zoiko (support or SRE), who can sign in to the platform console
and run commands on the server, but may not know this code.

| # | Runbook | Typical alert or sign |
|---|---|---|
| 01 | [Server errors](01-server-errors.md) | `availability_fast_burn`, `availability_slow_burn` |
| 02 | [Slow responses](02-slow-responses.md) | `latency_fast_burn`, `latency_slow_burn` |
| 03 | [Database unavailable](03-database-unavailable.md) | `/readyz` not ready; every page fails |
| 04 | [Email not going out](04-email-not-going-out.md) | mail "failed" or "held" growing |
| 05 | [Texts circuit open](05-texts-circuit-open.md) | Service levels: text provider circuit open |
| 06 | [Partner deliveries stuck](06-partner-deliveries-stuck.md) | Delivery attempts: dead letters |
| 07 | [Request flood](07-request-flood.md) | many 429 answers; "rate_limit" log lines |
| 08 | [Error budget used up](08-error-budget-exhausted.md) | `availability_exhausted`, `latency_exhausted`; releases held |

## Rules for every runbook

- **Patient safety first.** If clinical work may be affected (results, critical alerts, bookings),
  open an incident at once in the platform console (Incidents) and assess its severity. A SEV-0 or
  SEV-1 incident holds every release (DEV-183).
- **Never copy patient data** into a ticket, chat or log excerpt. Logs hold request ids and kinds
  of error only: quote those.
- **Write down what you did** in the incident's timeline as you go.
- **Do not switch a safety control off** (a `ZOIKO_*` setting set to `report` or `off`) to make an
  alert go away. The release check refuses such a server, and it hides the problem.
- **After the incident:** record the cause, and whether a runbook step was wrong. Fix the runbook.
