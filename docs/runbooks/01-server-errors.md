# 01: Server errors (5xx)

**Alert:** `availability_fast_burn` (over the last hour, errors at 14.4 times the rate the 99.9%
target allows) or `availability_slow_burn` (over 6 hours, 6 times).

## Check
1. Platform console, **Service levels**: which routes show server errors (route patterns only).
2. `GET /readyz` on the API. Not ready: go to [03 Database unavailable](03-database-unavailable.md).
3. Server logs (`docker compose logs backend --since 30m`, or the cloud log view): find
   `unhandled_exception type=... at=...` lines. They give the error class and the code line, never
   data.
4. Did a release go out in the last hours? Platform console, **Releases**.

## Act
- **One route failing after a release:** roll back to the previous build (DEPLOYMENT.md), then
  open an incident to find the cause.
- **Every route failing:** check the database (03), then the server's memory and disk.
- **One partner or provider down** (errors come from one integration): its circuit breaker should
  open by itself; see 05 or 06.
- If clinical work is affected (results, notes, bookings), open an incident and set its severity.

## Done when
The burn alert has cleared for 30 minutes and the cause is recorded in the incident.
