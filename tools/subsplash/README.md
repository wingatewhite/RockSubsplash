# Subsplash → Rock sync (Slingshot)

Tools that mirror Subsplash data (people, giving, groups, check-ins) into Rock RMS through Rock's
Bulk Import (Slingshot) so Subsplash can stay the system of record for giving, the app and the website.

| File | Purpose |
|------|---------|
| `subsplash_to_slingshot.py` | Subsplash CSV exports → `.slingshot` ZIP + `*.summary.json` + cumulative ledger |
| `rock_reconcile.py` | After a Bulk Import: reconciles Rock against the ledger and retires moved-from addresses |
| `generate_sample_data.py` | Fictional St. Mark's spec data (batch 1 and batch 2) for testing; never commit its output |
| `RUNBOOK.md` | The repeat flow for each new Subsplash export |

Python 3.9+ standard library only; no packages to install.

## Assumed Subsplash input schema

No real Subsplash export was available, so the shapes below come from Subsplash help center
articles (checked 2026-11-17) plus guesses. **Confirmed** = named in Subsplash documentation;
**Guessed** = assumed, verify against the first real export. Headers are matched
case- and punctuation-insensitively (`Donor First Name` = `donor_first_name`), and every field accepts
several aliases (see `PEOPLE_FIELDS`, `GIVING_FIELDS`, … at the top of `subsplash_to_slingshot.py`), so a
renamed column only needs one alias added there.

### people.csv — People > All People > Export

Source: "Advance Filtering, Emailing & Exporting People" (export is a CSV of profile data; `profile_id`
is the column Subsplash uses to match people on re-import) and "People & Household Import" / "Giving
Import" (the default person fields).

| Column | Status | Rock mapping |
|--------|--------|--------------|
| `profile_id` | **Confirmed** (exact name) | Person ForeignId (hashed) + `SubsplashProfileId` attribute |
| `first_name`, `last_name`, `email`, `phone` | Confirmed as fields; snake_case names guessed | FirstName, LastName, Email, Mobile phone |
| `date_of_birth`, `gender`, `marital_status` | Confirmed as fields; names guessed | Birthdate, Gender, MaritalStatus (Widowed → Unknown) |
| `street`, `street_2`, `city`, `state`, `postal_code`, `country` | Confirmed as fields; names guessed | Home address on the family |
| `membership_status`, `membership_status_date` | Confirmed as fields; names guessed | ConnectionStatus (Member/Attendee/Visitor…) + attributes |
| `external_id` | Confirmed ("External ID", used for envelope numbers) | `SubsplashExternalId` attribute |
| `grade_level` | Confirmed as field; name guessed | Grade |
| `household_id`, `household_name`, `household_role` (Adult/Child) | **Guessed** – Subsplash has Households with Adult/Child roles, but it is undocumented whether the People export includes them | Family (FamilyId/FamilyName/FamilyRole). Without them every person becomes a one-person family |
| `nickname`, `created_at`, `updated_at` | **Guessed** | NickName, Created/Modified dates |

### giving.csv — Giving > Transactions (or Transfers) > Export ("Simple Sync" CSV)

Source: "Export your Subsplash Giving data" (names these Simple Sync columns: Transaction ID, Transfer ID,
Transaction Date, Donor First Name, Donor Last Name, Gross Amount — called Give Amount when exported
from Transactions, Fund, Fund Code, Organization Member ID, and a status column that contains
"Balance Adjustment" rows) and the Giving Import template (Payment Method, Check Number, Memo, Campus).

| Column | Status | Rock mapping |
|--------|--------|--------------|
| `Transaction ID` | **Confirmed** | FinancialTransaction ForeignId (hashed); TransactionCode when there is no check number |
| `Transfer ID` | **Confirmed** | One FinancialBatch per transfer (bank deposit). Rows without one are batched per day |
| `Transaction Date` | **Confirmed** | TransactionDate |
| `Gross Amount` / `Give Amount` | **Confirmed** | FinancialTransactionDetail Amount |
| `Fund`, `Fund Code` | **Confirmed** | FinancialAccount (ForeignId from Fund Code, else fund name) |
| `Donor First Name`, `Donor Last Name` | **Confirmed** | used only for warnings |
| `Organization Member ID` | **Confirmed** | donor match fallback (envelope number) |
| `Status` | Confirmed that a status column exists; values guessed | Only Succeeded/Completed/Settled/… rows import; Balance Adjustment, Refunded, Failed, Pending are skipped and counted |
| `Payment Method`, `Check Number`, `Memo`, `Campus` | Confirmed as Giving Import fields; presence in the export guessed | CurrencyType, TransactionCode, Summary |
| `Donor Profile ID` | **Guessed** – needed for exact donor matching | AuthorizedPerson (falls back to `Donor Email`, then `Organization Member ID`) |
| `Donor Email`, `Fee Amount`, `Net Amount`, `Frequency`, `Source` | **Guessed** | email match; fee/frequency into Summary; TransactionSource (App/Text → Mobile Application, Kiosk, else Website) |

Gifts with no donor (loose plate cash) import as anonymous transactions. A transaction split across
funds may appear as several rows with the same Transaction ID; they become one transaction with one
detail per fund.

### groups.csv, group_members.csv, attendance.csv — **all guessed**

Subsplash has Groups and Check-in, but no documented CSV export was found. The transformer accepts:

* `groups.csv`: `group_id, name, description, group_type, meeting_schedule, is_public, is_active`
* `group_members.csv`: `group_id, profile_id, role` (any role name; Rock creates the role on the group type)
* `attendance.csv`: `checkin_id, profile_id, group_id, event_name, location, checkin_time, checkout_time`.
  Check-ins without a `group_id` (e.g. Sunday services) go to a group named after the event under a
  "Worship Services" group type; `event_name` becomes the Rock schedule and `location` a meeting location.

## How re-imports stay idempotent

* Every row gets an integer ForeignId derived from its Subsplash id (`IdMap` in the transformer) and
  Bulk Import is run with Foreign System Key `Subsplash`. Rock matches existing records on
  (ForeignKey, ForeignId), so a re-sent person is updated (with **Always Update**) and a re-sent gift,
  batch, account or check-in is skipped.
* Ids: person = `profile_id`; family = `household_id` (or the person's own id when they have no household);
  transaction = `Transaction ID`; detail = Transaction ID + fund + occurrence; batch = `Transfer ID`
  (or the gift date when there is none); account = `Fund Code` (or fund name); group = `group_id`;
  attendance = `checkin_id`. All-digit ids that fit in 31 bits are used as-is.
* `subsplash-ledger.json` accumulates everything ever sent (keyed by ForeignId) so `rock_reconcile.py`
  can compare Rock with the expected cumulative totals even when export windows overlap.

## Prototype results (St. Mark's spec data, 2026-11-17)

| | Batch 1 (May 3 – Oct 31) | Batch 2 (Oct 25 – Nov 15, one-week overlap) | Batch 2 again |
|---|---|---|---|
| People (Subsplash) | 86 imported (42 families) | 3 new (1 family), 2 email changes applied | 0 changes |
| Financial transactions | 842 (848 details, 52 batches) | 115 new; 22 overlap rows skipped | 0 new |
| Attendance | 1,479 | 194 new | 0 new |
| Groups / members | 6 groups, 48 members | +1 member (Bible Study) | unchanged |
| Rock totals after | 102,720.00 | 115,859.00 (4,200 Building, 2,520 Outreach, 109,139 Tithes) | 115,859.00 |
| `rock_reconcile.py` | reconciled | reconciled; 2 moved-from addresses set to Previous | reconciled |

Rock started with 5 people and no transactions; it ended with 94 people (89 from Subsplash, 0
duplicates), 957 transactions, 1,673 attendance records.

## What does not map cleanly

* **Integer ids** – Rock ForeignIds are integers; Subsplash ids are strings. Hashing is stable but the
  original id is only kept on people (`SubsplashProfileId`) and in TransactionCode for gifts.
* **Address history** – Bulk Import adds a changed address but never removes the old one. Run
  `rock_reconcile.py --retire-moved-addresses` after each import to turn the old Home address into Previous.
* **Deletes** – Records removed in Subsplash are not removed or inactivated in Rock. Group memberships
  are the exception: Bulk Import removes members missing from a re-sent group.
* **Refunds, failed and pending gifts, Balance Adjustment (GrowCurve rebates)** – skipped and counted
  in the summary. A gift refunded after it was imported stays in Rock; fix it by hand.
* **Processing fees / net amount** – Slingshot has no fee column. The fee goes into the transaction
  Summary text, not FinancialTransactionDetail.FeeAmount.
* **Recurring gift schedules, pledges, statements, payment tokens** – not exported by Subsplash, not imported.
* **Anonymous gifts** – gifts with no donor go to Rock's "Anonymous Giver" person.
* **Donor matching** – exact only when the giving export carries the donor's profile id (guessed
  column). Otherwise matching falls back to email, then Organization Member ID. Unmatched donors become
  anonymous gifts and show up in the summary warnings.
* **Households** – if the People export lacks household columns, everyone becomes a one-person family.
  Marital status Widowed/Separated maps to Unknown. Household "combined statement" preferences are not
  carried over.
* **Groups and check-ins** – the schemas are guesses. Meeting days/times go into the group description,
  not Rock schedules. Check-ins without a group create one group per event name.
* **Custom profile fields** – not mapped. Add them as `person-attribute.csv` entries in `transform_people`.
* **Campus** – everything goes to campus ForeignId 1. Multi-campus would need a campus lookup table.
* **Automation** – the import step is manual; see RUNBOOK.md.
