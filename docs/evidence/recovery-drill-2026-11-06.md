# Recovery drill, 2026-11-06 06:26 UTC

**Result: PASSED**

A backup of the development database was restored into a scratch database and checked.

| Check | Result |
|---|---|
| The live database did not change while the backup ran | passed |
| The backup restored into an empty database without errors | passed |
| Every one of the 44 tables came back with the same number of rows | passed |
| The audit chain of each of the 11 customers checks out in the restored copy (24155 entries) | passed |
| All 1 encrypted display names still decrypt with our keys | passed |

- Backup size: 5231 KiB. Backup took 1.1 s; restore took 3.1 s.
- Tables compared: 44. Rows in total: 49000.

**What this does not prove:** a regional failover, a restore on different hardware, a restore time at production size, or that backups are taken on a schedule and kept somewhere safe. Those need the hosting decisions (backlog, production setup).
