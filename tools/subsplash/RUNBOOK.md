# Subsplash → Rock sync runbook

Repeat this for every new set of Subsplash exports (weekly or after each Giving transfer is a
reasonable cadence). Paths below are the St. Mark's prototype box; adjust for production.

| What | Where |
|------|-------|
| Tools | `C:\Users\Administrator\repos\RockSubsplash\tools\subsplash` |
| Exports, .slingshot files, state | `C:\RockSync\subsplash\` (any folder outside git) |
| Id map + ledger (keep forever, back up) | `C:\RockSync\subsplash\state\subsplash-idmap.json`, `subsplash-ledger.json` |
| Rock Bulk Import page | `/admin/power-tools/bulk-import` (Admin Tools > Settings > Power Tools > Bulk Import) |

## One-time Rock setup (already done on the prototype)

1. Campus: the campus Subsplash data belongs to must have `ForeignKey = Subsplash` and
   `ForeignId = 1` (the transformer's `--campus-id`). On the prototype the default campus was renamed
   "St. Mark's Episcopal Church" and given those values. Otherwise Bulk Import creates a second campus.
2. Bulk Import page: Rock ships the Bulk Import block type but this install had no page for it; a page
   was added under Power Tools with route `admin/power-tools/bulk-import`.
3. Funds, group types, the "Worship Services" groups, schedules, locations and the `Subsplash` person
   attributes (`SubsplashProfileId`, `SubsplashExternalId`, `SubsplashMembershipStatus`,
   `SubsplashMembershipDate`) are created by the first import. To use existing Rock funds or group types
   instead, set their `ForeignKey` to `Subsplash` and `ForeignId` to the id the transformer assigns
   (see `subsplash-idmap.json`) before the first import.

## Each sync

1. **Export from Subsplash** into a new dated folder, e.g. `C:\RockSync\subsplash\2026-11-15\`:
   * People > All People > Export → save as `people.csv` (always a full export).
   * Giving > Transfers (or Transactions) > Export → `giving.csv`. Overlapping date ranges are fine:
     gifts are keyed by Transaction ID, so re-sent gifts update instead of duplicating.
   * Groups / check-ins if available → `groups.csv`, `group_members.csv`, `attendance.csv`.
2. **Transform**:
   ```
   cd C:\Users\Administrator\repos\RockSubsplash\tools\subsplash
   python subsplash_to_slingshot.py --input C:\RockSync\subsplash\2026-11-15 ^
       --output C:\RockSync\subsplash\out\stmarks-2026-11-15.slingshot ^
       --state C:\RockSync\subsplash\state\subsplash-idmap.json
   ```
   Read the printed counts and `stmarks-2026-11-15.summary.json`: `warnings` (unmatched donors,
   skipped rows), `missing_columns` (a renamed Subsplash column shows up here — add an alias in the
   `*_FIELDS` tables), and `skipped_gift_rows_by_status`.
3. **Import into Rock** (manual; see "Why the import is manual"):
   1. Sign in as an administrator and open `/admin/power-tools/bulk-import`.
   2. Foreign System Key: `Subsplash` (exactly; it is case-sensitive and ties every record to Subsplash).
   3. Select Slingshot File > Upload > pick the new `.slingshot`. Each file name can be uploaded once;
      "File already exists" means that name is already in `RockWeb\App_Data\SlingshotFiles` — use a new
      dated name or delete the old copy there.
   4. Advanced Settings > Import Options: **Always Update** (so changed names, emails, phones and
      memberships overwrite Rock's copy).
   5. Click **Import** and wait for "Import Complete". Expand Details for per-entity counts. Use
      Download Log if it reports errors.
4. **Reconcile and retire old addresses**:
   ```
   set ROCK_PASSWORD=<admin password>
   python rock_reconcile.py --ledger C:\RockSync\subsplash\state\subsplash-ledger.json --retire-moved-addresses
   ```
   Exit code 0 and `"reconciled": true` means people, transactions, transaction details, attendance and
   every fund total in Rock match everything ever sent from Subsplash, with no duplicate people.
   Exit code 2 means a mismatch: check the import log and the summary warnings.

## Why the import is manual

Bulk Import's `StartImport` block action (`POST /api/v2/BlockActions/{page}/{block}/StartImport`) joins a
RealTime channel with the browser's SignalR connection id before importing. Called from a script it
signs in fine, returns 200, then aborts in the background with `TaskCanceledException`
(BulkImportTool.cs line 210) and imports nothing. Rock has no Bulk Import job or REST endpoint. Options to
automate later: drive the page with a browser automation tool, or add a small Rock plugin/job that calls
`Rock.Slingshot.SlingshotImporter` directly on a schedule.

## Restoring / starting over

* Never delete `subsplash-idmap.json` or `subsplash-ledger.json`. The id map is what keeps ForeignIds
  stable if two Subsplash ids ever hash to the same number; the ledger is the reconciliation baseline.
* To wipe the Subsplash data from a test Rock, delete records where `ForeignKey = 'Subsplash'`, then
  delete both state files.
