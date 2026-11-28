# 02: Slow responses

**Alert:** `latency_fast_burn` or `latency_slow_burn`: too many requests take longer than 750 ms
(target: 95% within 750 ms).

## Check
1. Platform console, **Service levels**: which routes are slow.
2. The database: slow queries and connections (`pg_stat_activity`), and CPU and disk on the
   database server.
3. The worker: is a big job running at the same time (key rotation, a large import)?
4. Is there a request flood? See [07 Request flood](07-request-flood.md).

## Act
- **A slow route after a release:** roll back the release.
- **Database under load:** stop non-urgent jobs (`rotate_field_keys`, traceability imports) until
  it recovers. If this is normal traffic, add capacity.
- Never turn off rate limits to "let traffic through".

## Done when
The burn alert has cleared for 30 minutes.
