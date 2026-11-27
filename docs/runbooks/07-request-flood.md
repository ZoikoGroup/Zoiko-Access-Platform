# 07: Request flood

**Signs:** many 429 "Too many requests" answers; log lines `rate_limit would_refuse kind=...`
(report mode) or refusals; slow responses (02).

## Check
1. Which kind of limit:
   - `ip` or `ip_auth`: one network address (`ip_auth` is sign-in attempts);
   - `actor_read` or `actor_write`: one login;
   - `tenant`: one customer.
2. `ip_auth` refusals mean someone is guessing passwords. Sign-in lockouts also show in the audit
   log ("auth.login" denied).
3. One login or one customer: usually a broken integration or a script in a loop.

## Act
- **Password guessing:** keep the limits on, block the address at the load balancer if it
  continues, and open a security incident.
- **A customer's script in a loop:** tell the customer. Their limit protects everyone else.
- Safety-critical work (result review, notes) is not blocked by a customer's cap; doctors' own
  limits still apply.
- Do not raise a limit without recording why: the numbers are proposals (DEV-387).

## Done when
Refusals are back to normal and the source is known.
