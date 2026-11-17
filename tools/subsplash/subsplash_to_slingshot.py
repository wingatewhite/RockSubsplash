r"""Transform Subsplash CSV exports into a Rock RMS .slingshot file for Bulk Import.

Input folder (any subset; matched case-insensitively):
    people.csv          Subsplash People > All People > Export
    giving.csv          Subsplash Giving > Transactions (or Transfers) > Export ("Simple Sync" CSV)
    groups.csv          Subsplash groups (shape assumed, see README)
    group_members.csv   Subsplash group memberships (shape assumed)
    attendance.csv      Subsplash check-in / attendance (shape assumed)

Output: a .slingshot ZIP per Documentation/slingshot-file-format.md plus <name>.summary.json
with counts and per-fund totals for reconciliation.

Rock's Bulk Import keys every record by an integer ForeignId plus the Foreign System Key
(use "Subsplash"). Subsplash ids are opaque strings, so each one is mapped to a stable
31-bit integer (SHA-1 based, numeric ids kept as-is) and the mapping is persisted in a
state file so collisions are resolved the same way on every run.

Usage:
    python subsplash_to_slingshot.py --input C:\exports\2026-11-15 --output C:\exports\stmarks-2026-11-15.slingshot
"""

import argparse
import collections
import csv
import datetime as dt
import decimal
import hashlib
import io
import json
import os
import re
import sys
import zipfile

FOREIGN_SYSTEM_KEY = "Subsplash"
ROCK_DATE_FORMAT = "%m/%d/%Y %I:%M:%S %p"
INCLUDED_GIFT_STATUSES = {"", "succeeded", "success", "successful", "completed", "complete", "settled", "paid",
                          "deposited", "processed", "transferred", "approved"}

# Column aliases: first header present wins. Headers are normalized to snake_case before lookup,
# so "Donor First Name", "donor_first_name" and "DONOR FIRST NAME" all match.
PEOPLE_FIELDS = {
    "profile_id": ["profile_id", "person_id", "subsplash_id", "id"],
    "first_name": ["first_name", "firstname", "first"],
    "last_name": ["last_name", "lastname", "last"],
    "nickname": ["nickname", "preferred_name", "nick_name"],
    "middle_name": ["middle_name", "middle"],
    "email": ["email", "email_address", "primary_email"],
    "phone": ["phone", "mobile_phone", "cell_phone", "phone_number", "mobile"],
    "home_phone": ["home_phone"],
    "gender": ["gender", "sex"],
    "birthdate": ["date_of_birth", "birthdate", "birthday", "dob"],
    "marital_status": ["marital_status"],
    "anniversary": ["anniversary", "anniversary_date", "wedding_anniversary"],
    "membership_status": ["membership_status", "membership", "member_status"],
    "membership_status_date": ["membership_status_date"],
    "street": ["street", "street_1", "address", "address_1", "address_line_1"],
    "street_2": ["street_2", "address_2", "address_line_2"],
    "city": ["city"],
    "state": ["state", "region", "province"],
    "postal_code": ["postal_code", "zip", "zip_code", "zipcode"],
    "country": ["country"],
    "household_id": ["household_id", "family_id"],
    "household_name": ["household_name", "family_name", "household"],
    "household_role": ["household_role", "household_position", "family_role", "role"],
    "external_id": ["external_id", "organization_member_id", "envelope_number", "external_number"],
    "grade": ["grade_level", "grade"],
    "status": ["record_status", "status", "profile_status"],
    "created_at": ["created_at", "created", "date_created", "created_date"],
    "updated_at": ["updated_at", "modified", "modified_at", "last_modified", "date_modified", "updated"],
}
GIVING_FIELDS = {
    "transaction_id": ["transaction_id", "gift_id", "donation_id", "id"],
    "transfer_id": ["transfer_id", "payout_id", "deposit_id", "batch_id"],
    "date": ["transaction_date", "donation_date", "gift_date", "date", "created_at"],
    "status": ["status", "payment_status", "transaction_status"],
    "profile_id": ["donor_profile_id", "profile_id", "donor_id", "person_id"],
    "first_name": ["donor_first_name", "first_name"],
    "last_name": ["donor_last_name", "last_name"],
    "email": ["donor_email", "email"],
    "external_id": ["organization_member_id", "external_id", "external_number", "envelope_number"],
    "fund": ["fund", "fund_name"],
    "fund_code": ["fund_code", "custom_fund_id", "fund_id"],
    "amount": ["gross_amount", "give_amount", "amount", "gift_amount"],
    "fee": ["fee_amount", "fee", "processing_fee", "fees"],
    "payment_method": ["payment_method", "payment_type", "method", "card_type"],
    "check_number": ["check_number", "check_no"],
    "frequency": ["frequency", "gift_frequency", "recurrence"],
    "source": ["source", "platform", "giving_source", "channel"],
    "memo": ["memo", "gift_description", "note", "notes"],
}
GROUP_FIELDS = {
    "group_id": ["group_id", "id"],
    "name": ["name", "group_name"],
    "description": ["description"],
    "group_type": ["group_type", "type", "category"],
    "meeting_schedule": ["meeting_schedule", "schedule", "meeting_time"],
    "is_public": ["is_public", "public", "visibility"],
    "is_active": ["is_active", "active", "status"],
}
MEMBER_FIELDS = {
    "group_id": ["group_id"],
    "profile_id": ["profile_id", "person_id", "member_profile_id"],
    "role": ["role", "member_role", "group_role"],
}
ATTENDANCE_FIELDS = {
    "attendance_id": ["checkin_id", "check_in_id", "attendance_id", "id"],
    "profile_id": ["profile_id", "person_id"],
    "group_id": ["group_id"],
    "event_name": ["event_name", "event", "service", "session_name"],
    "location": ["location", "room", "location_name"],
    "start": ["checkin_time", "check_in_time", "start_time", "start", "date"],
    "end": ["checkout_time", "check_out_time", "end_time", "end"],
}


class IdMap:
    """Maps Subsplash string ids to stable Rock ForeignIds (positive 31-bit ints) per entity type."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        self.used = {k: set(v.values()) for k, v in self.data.items()}

    def get(self, entity, key):
        key = str(key).strip()
        table = self.data.setdefault(entity, {})
        used = self.used.setdefault(entity, set())
        if key in table:
            return table[key]
        if key.isdigit() and 0 < int(key) < 2 ** 31 and int(key) not in used:
            value = int(key)
        else:
            value = int(hashlib.sha1(f"{entity}|{key}".encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
            while value == 0 or value in used:  # resolve collisions deterministically, then persist
                value = (value + 1) & 0x7FFFFFFF
        table[key] = value
        used.add(value)
        return value

    def save(self):
        if self.path:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1, sort_keys=True)


def normalize_header(h):
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def find_file(folder, *names):
    lookup = {normalize_header(os.path.splitext(n)[0]): n for n in os.listdir(folder) if n.lower().endswith(".csv")}
    for name in names:
        if normalize_header(name) in lookup:
            return os.path.join(folder, lookup[normalize_header(name)])
    return None


def read_rows(path, fields):
    """Reads a CSV and returns (rows keyed by canonical field name, canonical fields with no matching column)."""
    if not path:
        return [], list(fields)
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        headers = [normalize_header(h) for h in next(reader, [])]
        index = {}
        for field, aliases in fields.items():
            for alias in aliases:
                if alias in headers:
                    index[field] = headers.index(alias)
                    break
        missing = [k for k in fields if k not in index]
        rows = []
        for raw in reader:
            if not any(c.strip() for c in raw):
                continue
            row = {k: (raw[i].strip() if i < len(raw) else "") for k, i in index.items()}
            row.update({k: "" for k in missing})
            rows.append(row)
        return rows, missing


DATE_FORMATS = ["%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%y",
                "%b %d, %Y", "%B %d, %Y", "%b %d, %Y %I:%M %p"]


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    value = re.sub(r"(Z|[+-]\d\d:?\d\d)$", "", value)
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date '{value}'")


def rock_date(value):
    d = value if isinstance(value, dt.datetime) else parse_date(value)
    return d.strftime(ROCK_DATE_FORMAT) if d else ""


def money(value):
    value = re.sub(r"[^0-9.\-()]", "", value or "")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    return decimal.Decimal(value or "0").quantize(decimal.Decimal("0.01"))


def truthy(value, default=True):
    v = (value or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "active", "public", "published")


def map_gender(v):
    v = (v or "").strip().lower()
    return "Male" if v in ("m", "male", "man") else "Female" if v in ("f", "female", "woman") else "Unknown"


def map_marital(v):
    v = (v or "").strip().lower()
    if v.startswith("married"):
        return "Married"
    if v.startswith("divorced"):
        return "Divorced"
    if v.startswith("single"):
        return "Single"
    return "Unknown"  # Widowed/Separated have no Slingshot enum value


def map_connection_status(v):
    v = (v or "").strip()
    known = {"member": "Member", "visitor": "Visitor", "guest": "Visitor", "first time guest": "Visitor",
             "regular attender": "Attendee", "attender": "Attendee", "attendee": "Attendee",
             "participant": "Participant", "prospect": "Prospect"}
    return known.get(v.lower(), v.title() if v else "Attendee")


def is_child(row, as_of):
    role = (row["household_role"] or "").lower()
    if "child" in role or "dependent" in role:
        return True
    if "adult" in role or "head" in role or "spouse" in role:
        return False
    born = parse_date(row["birthdate"])
    return bool(born and (as_of - born).days < 18 * 365.25)


def transform_people(rows, ids, out, report, campus_id, campus_name, as_of):
    people_by_profile, people_by_email, people_by_external = {}, {}, {}
    for row in rows:
        profile_id = row["profile_id"]
        if not profile_id:
            report["warnings"].append(f"Skipped person without profile id: {row['first_name']} {row['last_name']}")
            continue
        person_id = ids.get("person", profile_id)
        household_key = row["household_id"] or f"solo:{profile_id}"
        family_id = ids.get("family", household_key)
        family_name = row["household_name"] or row["last_name"]
        family_name = re.sub(r"\s+(household|family)$", "", family_name, flags=re.I) or row["last_name"]
        status = "Inactive" if (row["status"] or "").lower() in ("inactive", "archived", "deleted") else "Active"
        out["person.csv"].append({
            "Id": person_id, "FamilyId": family_id, "FamilyName": family_name, "FamilyImageUrl": "",
            "FamilyRole": "Child" if is_child(row, as_of) else "Adult",
            "FirstName": row["first_name"], "NickName": row["nickname"], "LastName": row["last_name"],
            "MiddleName": row["middle_name"], "Salutation": "", "Suffix": "", "Email": row["email"],
            "Gender": map_gender(row["gender"]), "MaritalStatus": map_marital(row["marital_status"]),
            "Birthdate": rock_date(row["birthdate"]), "AnniversaryDate": rock_date(row["anniversary"]),
            "RecordStatus": status, "InactiveReason": "", "ConnectionStatus": map_connection_status(
                row["membership_status"]), "EmailPreference": "EmailAllowed",
            "CreatedDateTime": rock_date(row["created_at"]), "ModifiedDateTime": rock_date(row["updated_at"]),
            "PersonPhotoUrl": "", "CampusId": campus_id, "CampusName": campus_name, "Note": "",
            "Grade": row["grade"], "GiveIndividually": "False", "IsDeceased": "False"})
        if row["street"]:
            out["person-address.csv"].append({
                "PersonId": person_id, "Street1": row["street"], "Street2": row["street_2"], "City": row["city"],
                "State": row["state"], "PostalCode": row["postal_code"], "Country": row["country"],
                "Latitude": "", "Longitude": "", "IsMailing": "True", "AddressType": "Home"})
        if row["phone"]:
            out["person-phone.csv"].append({"PersonId": person_id, "PhoneType": "Mobile",
                                            "PhoneNumber": row["phone"], "IsMessagingEnabled": "True",
                                            "IsUnlisted": "False"})
        if row["home_phone"]:
            out["person-phone.csv"].append({"PersonId": person_id, "PhoneType": "Home",
                                            "PhoneNumber": row["home_phone"], "IsMessagingEnabled": "False",
                                            "IsUnlisted": "False"})
        for key, value in (("SubsplashProfileId", profile_id), ("SubsplashExternalId", row["external_id"]),
                           ("SubsplashMembershipStatus", row["membership_status"]),
                           ("SubsplashMembershipDate", rock_date(row["membership_status_date"]))):
            if value:
                out["person-attributevalue.csv"].append({"PersonId": person_id, "AttributeKey": key,
                                                         "AttributeValue": value})
        people_by_profile[profile_id] = person_id
        if row["email"]:
            people_by_email.setdefault(row["email"].lower(), person_id)
        if row["external_id"]:
            people_by_external[row["external_id"]] = person_id
    out["person-attribute.csv"].extend([
        {"Key": "SubsplashProfileId", "Name": "Subsplash Profile Id",
         "FieldType": "Rock.Field.Types.TextFieldType", "Category": "Subsplash"},
        {"Key": "SubsplashExternalId", "Name": "Subsplash External Id (Envelope #)",
         "FieldType": "Rock.Field.Types.TextFieldType", "Category": "Subsplash"},
        {"Key": "SubsplashMembershipStatus", "Name": "Subsplash Membership Status",
         "FieldType": "Rock.Field.Types.TextFieldType", "Category": "Subsplash"},
        {"Key": "SubsplashMembershipDate", "Name": "Subsplash Membership Status Date",
         "FieldType": "Rock.Field.Types.DateFieldType", "Category": "Subsplash"}])
    report["counts"]["people"] = len(out["person.csv"])
    report["counts"]["families"] = len({p["FamilyId"] for p in out["person.csv"]})
    return people_by_profile, people_by_email, people_by_external


def map_currency(method):
    m = (method or "").lower()
    if any(k in m for k in ("visa", "master", "amex", "american express", "discover", "diners", "jcb", "card",
                            "apple pay", "google pay")):
        return "CreditCard"
    if "ach" in m or "bank" in m:
        return "ACH"
    if "check" in m or "cheque" in m:
        return "Check"
    if "cash" in m:
        return "Cash"
    if any(k in m for k in ("stock", "daf", "qcd", "bond", "crypto", "goods", "property", "non")):
        return "NonCash"
    return "Other" if m else "Unknown"


def map_source(source, currency):
    s = (source or "").lower()
    if "app" in s or "text" in s or "mobile" in s:
        return "MobileApplication"
    if "kiosk" in s:
        return "Kiosk"
    if currency in ("Cash", "Check"):
        return "OnsiteCollection"
    return "Website"


def transform_giving(rows, ids, out, report, campus_id, people_by_profile, people_by_email, people_by_external):
    accounts, batches, transactions = {}, {}, collections.OrderedDict()
    skipped = collections.Counter()
    unmatched = 0
    for row in rows:
        status = (row["status"] or "").strip().lower()
        if status not in INCLUDED_GIFT_STATUSES:
            skipped[row["status"]] += 1
            continue
        if not row["transaction_id"]:
            raise ValueError(f"Giving row without a transaction id: {row}")
        fund_key = row["fund_code"] or row["fund"]
        account_id = ids.get("account", fund_key)
        accounts.setdefault(account_id, row["fund"] or fund_key)
        when = parse_date(row["date"])
        if row["transfer_id"]:
            batch_key, batch_name = row["transfer_id"], f"Subsplash Transfer {row['transfer_id']}"
        else:
            batch_key = f"day:{when:%Y-%m-%d}"
            batch_name = f"Subsplash Gifts (no transfer) {when:%m/%d/%Y}"
        batch_id = ids.get("batch", batch_key)
        batch = batches.setdefault(batch_id, {"name": batch_name, "start": when, "end": when,
                                              "total": decimal.Decimal("0")})
        batch["start"], batch["end"] = min(batch["start"], when), max(batch["end"], when)
        amount = money(row["amount"])
        batch["total"] += amount

        tx_id = ids.get("transaction", row["transaction_id"])
        tx = transactions.get(tx_id)
        if tx is None:
            donor = (people_by_profile.get(row["profile_id"]) or people_by_email.get(row["email"].lower())
                     or people_by_external.get(row["external_id"]))
            if donor is None and (row["profile_id"] or row["email"] or row["first_name"]):
                unmatched += 1
                report["warnings"].append(f"Gift {row['transaction_id']} donor not found in people export; "
                                          f"imported as anonymous")
            currency = map_currency(row["payment_method"])
            summary = "; ".join(x for x in (row["memo"], f"Frequency: {row['frequency']}" if row["frequency"] else "",
                                             f"Fee: {money(row['fee'])}" if row["fee"] else "") if x)
            tx = {"Id": tx_id, "BatchId": batch_id, "AuthorizedPersonId": donor or "",
                  "TransactionDate": rock_date(when), "TransactionType": "Contribution",
                  "TransactionSource": map_source(row["source"], currency), "CurrencyType": currency,
                  "Summary": summary, "TransactionCode": row["check_number"] or row["transaction_id"],
                  "CreatedByPersonId": "", "CreatedDateTime": rock_date(when), "ModifiedByPersonId": "",
                  "ModifiedDateTime": "", "_details": collections.Counter()}
            transactions[tx_id] = tx
        tx["_details"][fund_key] += 1
        detail_id = ids.get("transaction_detail", f"{row['transaction_id']}|{fund_key}|{tx['_details'][fund_key]}")
        out["financial-transactiondetail.csv"].append({
            "Id": detail_id, "TransactionId": tx_id, "AccountId": account_id, "Amount": f"{amount:.2f}",
            "Summary": row["memo"], "CreatedByPersonId": "", "CreatedDateTime": rock_date(when),
            "ModifiedByPersonId": "", "ModifiedDateTime": ""})
        fund_total = report["fund_totals"].setdefault(row["fund"] or fund_key, "0.00")
        report["fund_totals"][row["fund"] or fund_key] = f"{money(fund_total) + amount:.2f}"

    for account_id, name in sorted(accounts.items()):
        out["financial-account.csv"].append({"Id": account_id, "Name": name, "IsTaxDeductible": "True",
                                             "CampusId": campus_id, "ParentAccountId": ""})
    for batch_id, b in batches.items():
        out["financial-batch.csv"].append({
            "Id": batch_id, "Name": b["name"], "CampusId": campus_id, "StartDate": rock_date(b["start"]),
            "EndDate": rock_date(b["end"]), "Status": "Closed", "CreatedByPersonId": "",
            "CreatedDateTime": rock_date(b["start"]), "ModifiedByPersonId": "", "ModifiedDateTime": "",
            "ControlAmount": f"{b['total']:.2f}"})
    for tx in transactions.values():
        tx.pop("_details")
        out["financial-transaction.csv"].append(tx)
    report["counts"].update({"financial_accounts": len(accounts), "financial_batches": len(batches),
                             "financial_transactions": len(transactions),
                             "financial_transaction_details": len(out["financial-transactiondetail.csv"]),
                             "anonymous_transactions": sum(1 for t in transactions.values()
                                                           if not t["AuthorizedPersonId"]),
                             "unmatched_donors": unmatched})
    report["gift_total"] = f"{sum((money(d['Amount']) for d in out['financial-transactiondetail.csv']), decimal.Decimal('0')):.2f}"
    report["skipped_gift_rows_by_status"] = dict(skipped)


def transform_groups(group_rows, member_rows, ids, out, report, campus_id, people_by_profile):
    group_ids = {}
    for row in group_rows:
        if not row["group_id"]:
            continue
        type_name = row["group_type"] or "Subsplash Group"
        type_id = ids.get("grouptype", type_name)
        if not any(t["Id"] == type_id for t in out["grouptype.csv"]):
            out["grouptype.csv"].append({"Id": type_id, "Name": type_name})
        group_id = ids.get("group", row["group_id"])
        group_ids[row["group_id"]] = group_id
        description = row["description"]
        if row["meeting_schedule"]:
            description = f"{description} (Meets: {row['meeting_schedule']})".strip()
        out["group.csv"].append({
            "Id": group_id, "Name": row["name"], "Description": description, "Order": 0, "ParentGroupId": 0,
            "GroupTypeId": type_id, "CampusId": campus_id, "Capacity": "", "MeetingDay": "", "MeetingTime": "",
            "IsActive": str(truthy(row["is_active"])), "IsPublic": str(truthy(row["is_public"]))})
    missing = 0
    for row in member_rows:
        person_id = people_by_profile.get(row["profile_id"])
        group_id = group_ids.get(row["group_id"])
        if person_id is None or group_id is None:
            missing += 1
            continue
        out["groupmember.csv"].append({"PersonId": person_id, "GroupId": group_id, "Role": row["role"] or "Member"})
    if missing:
        report["warnings"].append(f"{missing} group member rows skipped (unknown person or group)")
    report["counts"].update({"group_types": len(out["grouptype.csv"]), "groups": len(out["group.csv"]),
                             "group_members": len(out["groupmember.csv"])})
    return group_ids


def transform_attendance(rows, ids, out, report, campus_id, people_by_profile, group_ids):
    """Attendance needs a group; check-ins without one go to a group per event under 'Worship Services'."""
    missing = 0
    for row in rows:
        person_id = people_by_profile.get(row["profile_id"])
        start = parse_date(row["start"])
        if person_id is None or start is None or not row["attendance_id"]:
            missing += 1
            continue
        event = row["event_name"] or "Subsplash Check-in"
        group_id = group_ids.get(row["group_id"])
        if group_id is None:
            type_id = ids.get("grouptype", "Worship Services")
            if not any(t["Id"] == type_id for t in out["grouptype.csv"]):
                out["grouptype.csv"].append({"Id": type_id, "Name": "Worship Services"})
            group_id = ids.get("group", f"event:{event}")
            if group_id not in group_ids.values():
                group_ids[f"event:{event}"] = group_id
                out["group.csv"].append({
                    "Id": group_id, "Name": event, "Description": "Created from Subsplash check-ins", "Order": 0,
                    "ParentGroupId": 0, "GroupTypeId": type_id, "CampusId": campus_id, "Capacity": "",
                    "MeetingDay": "", "MeetingTime": "", "IsActive": "True", "IsPublic": "True"})
        schedule_id = ids.get("schedule", event)
        if not any(s["Id"] == schedule_id for s in out["schedule.csv"]):
            out["schedule.csv"].append({"Id": schedule_id, "Name": event})
        location_id = ""
        if row["location"]:
            location_id = ids.get("location", row["location"])
            if not any(l["Id"] == location_id for l in out["locations.csv"]):
                out["locations.csv"].append({
                    "Id": location_id, "ParentLocationId": "", "Name": row["location"], "IsActive": "True",
                    "LocationType": "MeetingLocation", "Street1": "", "Street2": "", "City": "", "State": "",
                    "Country": "", "PostalCode": "", "County": ""})
        end = parse_date(row["end"])
        out["attendance.csv"].append({
            "AttendanceId": ids.get("attendance", row["attendance_id"]), "PersonId": person_id,
            "GroupId": group_id, "LocationId": location_id, "ScheduleId": schedule_id, "DeviceId": "",
            "StartDateTime": rock_date(start), "EndDateTime": rock_date(end) if end else "", "Note": "",
            "CampusId": campus_id})
    if missing:
        report["warnings"].append(f"{missing} attendance rows skipped (unknown person, id or time)")
    report["counts"].update({"attendance": len(out["attendance.csv"]), "schedules": len(out["schedule.csv"]),
                             "locations": len(out["locations.csv"]), "group_types": len(out["grouptype.csv"]),
                             "groups": len(out["group.csv"])})


# Column order from Documentation/slingshot-file-format.md.
SLINGSHOT_COLUMNS = {
    "person.csv": "Id,FamilyId,FamilyName,FamilyImageUrl,FamilyRole,FirstName,NickName,LastName,MiddleName,"
                  "Salutation,Suffix,Email,Gender,MaritalStatus,Birthdate,AnniversaryDate,RecordStatus,"
                  "InactiveReason,ConnectionStatus,EmailPreference,CreatedDateTime,ModifiedDateTime,PersonPhotoUrl,"
                  "CampusId,CampusName,Note,Grade,GiveIndividually,IsDeceased",
    "person-address.csv": "PersonId,Street1,Street2,City,State,PostalCode,Country,Latitude,Longitude,IsMailing,"
                          "AddressType",
    "person-phone.csv": "PersonId,PhoneType,PhoneNumber,IsMessagingEnabled,IsUnlisted",
    "person-attribute.csv": "Key,Name,FieldType,Category",
    "person-attributevalue.csv": "PersonId,AttributeKey,AttributeValue",
    "financial-account.csv": "Id,Name,IsTaxDeductible,CampusId,ParentAccountId",
    "financial-batch.csv": "Id,Name,CampusId,StartDate,EndDate,Status,CreatedByPersonId,CreatedDateTime,"
                           "ModifiedByPersonId,ModifiedDateTime,ControlAmount",
    "financial-transaction.csv": "Id,BatchId,AuthorizedPersonId,TransactionDate,TransactionType,TransactionSource,"
                                 "CurrencyType,Summary,TransactionCode,CreatedByPersonId,CreatedDateTime,"
                                 "ModifiedByPersonId,ModifiedDateTime",
    "financial-transactiondetail.csv": "Id,TransactionId,AccountId,Amount,Summary,CreatedByPersonId,"
                                       "CreatedDateTime,ModifiedByPersonId,ModifiedDateTime",
    "grouptype.csv": "Id,Name",
    "group.csv": "Id,Name,Description,Order,ParentGroupId,GroupTypeId,CampusId,Capacity,MeetingDay,MeetingTime,"
                 "IsActive,IsPublic",
    "groupmember.csv": "PersonId,GroupId,Role",
    "locations.csv": "Id,ParentLocationId,Name,IsActive,LocationType,Street1,Street2,City,State,Country,PostalCode,"
                     "County",
    "schedule.csv": "Id,Name",
    "attendance.csv": "AttendanceId,PersonId,GroupId,LocationId,ScheduleId,DeviceId,StartDateTime,EndDateTime,Note,"
                      "CampusId",
}


def update_ledger(path, out, report):
    """Cumulative record of everything sent to Rock, keyed by ForeignId, used to reconcile after each import.

    Re-sent records overwrite their own entry, so overlapping exports are counted once.
    """
    ledger = {"people": {}, "transactions": {}, "details": {}, "attendance": {}, "family_addresses": {}}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            ledger.update(json.load(f))
    accounts = {str(a["Id"]): a["Name"] for a in out["financial-account.csv"]}
    for p in out["person.csv"]:
        ledger["people"][str(p["Id"])] = f"{p['FirstName']} {p['LastName']}"
    family_of = {str(p["Id"]): str(p["FamilyId"]) for p in out["person.csv"]}
    ledger.setdefault("family_addresses", {})
    for a in out["person-address.csv"]:  # latest export wins; used to retire moved-from addresses
        ledger["family_addresses"][family_of[str(a["PersonId"])]] = {
            "Street1": a["Street1"], "Street2": a["Street2"], "City": a["City"], "State": a["State"],
            "PostalCode": a["PostalCode"]}
    for t in out["financial-transaction.csv"]:
        ledger["transactions"][str(t["Id"])] = t["TransactionCode"]
    for d in out["financial-transactiondetail.csv"]:
        ledger["details"][str(d["Id"])] = [accounts[str(d["AccountId"])], d["Amount"]]
    for a in out["attendance.csv"]:
        ledger["attendance"][str(a["AttendanceId"])] = a["StartDateTime"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1, sort_keys=True)
    totals = collections.defaultdict(decimal.Decimal)
    for fund, amount in ledger["details"].values():
        totals[fund] += decimal.Decimal(amount)
    report["cumulative"] = {"people": len(ledger["people"]), "financial_transactions": len(ledger["transactions"]),
                            "financial_transaction_details": len(ledger["details"]),
                            "attendance": len(ledger["attendance"]),
                            "fund_totals": {k: f"{v:.2f}" for k, v in sorted(totals.items())}}


def write_slingshot(out, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, columns in SLINGSHOT_COLUMNS.items():
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=columns.split(","), lineterminator="\r\n")
            w.writeheader()
            w.writerows(out[name])
            z.writestr(name, buf.getvalue())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="Folder containing the Subsplash CSV exports")
    ap.add_argument("--output", required=True, help="Path of the .slingshot file to write")
    ap.add_argument("--state", help="Id map JSON kept between runs (default: subsplash-idmap.json next to output)")
    ap.add_argument("--campus-id", type=int, default=1, help="Campus ForeignId (Rock campus with ForeignKey=Subsplash)")
    ap.add_argument("--campus-name", default="St. Mark's Episcopal Church")
    args = ap.parse_args(argv)

    state = args.state or os.path.join(os.path.dirname(os.path.abspath(args.output)), "subsplash-idmap.json")
    ids = IdMap(state)
    out = {name: [] for name in SLINGSHOT_COLUMNS}
    report = {"input": os.path.abspath(args.input), "foreign_system_key": FOREIGN_SYSTEM_KEY, "counts": {},
              "fund_totals": {}, "warnings": [], "missing_columns": {}}

    def load(fields, *names):
        path = find_file(args.input, *names)
        rows, missing = read_rows(path, fields)
        if path:
            report["missing_columns"][os.path.basename(path)] = missing
        return rows

    people = load(PEOPLE_FIELDS, "people", "people_export", "all_people", "profiles")
    giving = load(GIVING_FIELDS, "giving", "transactions", "gifts", "transfers", "giving_export")
    groups = load(GROUP_FIELDS, "groups")
    members = load(MEMBER_FIELDS, "group_members", "groupmembers", "group_membership")
    attendance = load(ATTENDANCE_FIELDS, "attendance", "checkins", "check_ins")

    lookups = transform_people(people, ids, out, report, args.campus_id, args.campus_name, dt.datetime.now())
    transform_giving(giving, ids, out, report, args.campus_id, *lookups)
    group_ids = transform_groups(groups, members, ids, out, report, args.campus_id, lookups[0])
    transform_attendance(attendance, ids, out, report, args.campus_id, lookups[0], group_ids)

    ledger_path = os.path.join(os.path.dirname(os.path.abspath(state)), "subsplash-ledger.json")
    update_ledger(ledger_path, out, report)

    write_slingshot(out, args.output)
    ids.save()
    summary_path = os.path.splitext(args.output)[0] + ".summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: report[k] for k in ("counts", "fund_totals", "gift_total", "skipped_gift_rows_by_status", "cumulative")
                      if k in report}, indent=2))
    if report["warnings"]:
        print(f"{len(report['warnings'])} warning(s); see {summary_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
