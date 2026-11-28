# 08: Error budget used up

**Alert:** `availability_exhausted` or `latency_exhausted`: over 30 days, more requests failed (or
were slow) than the 99.9% and 95% targets allow.

**Effect:** every release in the go-live readiness register is held (`g3.error_budget:missing`),
as the error-budget policy requires (GHE-18).

## Check
1. Platform console, **Service levels**: which budget, how much is used, and the burn rates.
2. Which incidents used it up (Incidents)? Is the cause fixed?

## Act
- Spend the time on reliability: fixes for the causes, not new features.
- A release that FIXES the problem may go ahead only with a safety officer's override: platform
  console, **Go-live readiness**, the release, "Override the error-budget hold". It needs a
  reason, lasts at most 7 days, and asks for the two-step code again. The override is shown on the
  release and audited.
- Never switch request counting off (`ZOIKO_SLO_RECORDING`): the release check refuses that.

## Done when
The budget is no longer used up (failures age out of the 30-day window as the service improves),
or each release that went ahead had a recorded override.
