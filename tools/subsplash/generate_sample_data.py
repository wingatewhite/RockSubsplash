r"""Generate fictional Subsplash-shaped CSV exports for St. Mark's Episcopal Church.

All names, addresses, emails and phone numbers are synthetic (example.com emails,
904-555-01xx numbers). Output is meant for testing the Subsplash -> Rock sync and
must not be committed.

Usage:
    python generate_sample_data.py --out C:\RockSetup\subsplash\batch1 --batch 1
    python generate_sample_data.py --out C:\RockSetup\subsplash\batch2 --batch 2

Batch 1 is a full People export plus giving/attendance from 2026-05-03 to 2026-10-31.
Batch 2 is the next export: the full People list again (with a few changed addresses
and emails and one new household) plus giving/attendance from 2026-10-25 to
2026-11-15. The one-week overlap with batch 1 proves re-imports do not duplicate.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random

SEED = 1817  # St. Mark's founding-ish year; any constant keeps the data stable.

FIRST_MALE = ["James", "John", "Robert", "Michael", "William", "David", "Richard", "Thomas", "Charles", "Daniel",
              "Matthew", "Andrew", "Joshua", "Samuel", "Henry", "Peter", "Paul", "George", "Edward", "Walter"]
FIRST_FEMALE = ["Mary", "Elizabeth", "Sarah", "Margaret", "Catherine", "Anne", "Grace", "Emily", "Hannah", "Laura",
                "Rebecca", "Julia", "Caroline", "Eleanor", "Abigail", "Charlotte", "Lucy", "Ruth", "Claire", "Helen"]
KIDS_M = ["Owen", "Caleb", "Luke", "Isaac", "Jack", "Eli", "Noah", "Miles"]
KIDS_F = ["Ava", "Lily", "Nora", "Zoe", "Ella", "Maya", "Ivy", "Rose"]
LAST = ["Ashby", "Bennett", "Calloway", "Dunmore", "Ellison", "Fairchild", "Garrison", "Holloway", "Ingram", "Jarvis",
        "Kendrick", "Lockhart", "Merriweather", "Northcutt", "Ogden", "Pembroke", "Quinlan", "Radford", "Stanhope",
        "Thackery", "Underwood", "Vance", "Whitfield", "Yardley", "Alcott", "Brantley", "Crowder", "Delacroix",
        "Everly", "Fenwick", "Gilchrist", "Hargrove", "Kimball", "Langston", "Mabry", "Nesbitt", "Orwell",
        "Prescott", "Rutledge", "Sinclair", "Tolliver", "Wakefield", "Bramlett", "Chandler", "Danforth"]
STREETS = [("San Jose Blvd", "32207"), ("Riverside Ave", "32204"), ("Hendricks Ave", "32207"),
           ("Herschel St", "32205"), ("St Johns Ave", "32205"), ("Park St", "32204"), ("Atlantic Blvd", "32207"),
           ("Baymeadows Rd", "32256"), ("Mandarin Rd", "32223"), ("Beach Blvd", "32246"),
           ("Old St Augustine Rd", "32257"), ("Timuquana Rd", "32210"), ("Sunbeam Rd", "32257"),
           ("Hood Rd", "32257"), ("Roosevelt Blvd", "32210"), ("Avondale Ave", "32205")]
FUNDS = [("Tithes & Offerings", "100"), ("Building Fund", "200"), ("Outreach", "300")]
CARD_METHODS = ["Visa", "Mastercard", "American Express", "Discover", "ACH"]
SOURCES = ["App", "Web", "Text", "Kiosk"]

B1_START, B1_END = dt.date(2026, 5, 3), dt.date(2026, 10, 31)
B2_START, B2_END = dt.date(2026, 10, 25), dt.date(2026, 11, 15)

PEOPLE_COLUMNS = ["profile_id", "first_name", "last_name", "nickname", "email", "phone", "gender", "date_of_birth",
                  "marital_status", "membership_status", "membership_status_date", "street", "street_2", "city",
                  "state", "postal_code", "country", "household_id", "household_name", "household_role",
                  "external_id", "created_at", "updated_at"]
GIVING_COLUMNS = ["Transaction ID", "Transfer ID", "Transaction Date", "Status", "Donor Profile ID",
                  "Donor First Name", "Donor Last Name", "Donor Email", "Organization Member ID", "Fund", "Fund Code",
                  "Gross Amount", "Fee Amount", "Net Amount", "Payment Method", "Check Number", "Frequency",
                  "Source", "Campus", "Memo"]
GROUP_COLUMNS = ["group_id", "name", "description", "group_type", "meeting_schedule", "is_public", "is_active"]
GROUP_MEMBER_COLUMNS = ["group_id", "profile_id", "role", "joined_at"]
ATTENDANCE_COLUMNS = ["checkin_id", "profile_id", "group_id", "event_name", "location", "checkin_time",
                      "checkout_time"]


def sid(prefix, *parts):
    """Stable Subsplash-like string id (Subsplash ids are opaque strings, not integers)."""
    return prefix + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def sundays(start, end):
    d = start + dt.timedelta(days=(6 - start.weekday()) % 7)
    while d <= end:
        yield d
        d += dt.timedelta(days=7)


def build_people(rng):
    people, households = [], []
    used_names = set()
    phone_seq = iter(range(100, 200))

    def new_person(first, last, gender, dob, hh, role, marital):
        n = len(people) + 1
        email = f"{first}.{last}{n}@example.com".lower() if role == "Adult" or rng.random() < 0.3 else ""
        p = {
            "profile_id": sid("prf_", "stmarks", n), "first_name": first, "last_name": last,
            "nickname": "", "email": email,
            "phone": f"904-555-0{next(phone_seq)}" if role == "Adult" else "",
            "gender": gender, "date_of_birth": dob.strftime("%m/%d/%Y") if dob else "",
            "marital_status": marital,
            "membership_status": rng.choice(["Member"] * 6 + ["Regular Attender"] * 3 + ["Visitor"]),
            "membership_status_date": "", "street": hh["street"], "street_2": hh["street_2"],
            "city": "Jacksonville", "state": "FL", "postal_code": hh["zip"], "country": "US",
            "household_id": hh["household_id"], "household_name": hh["household_name"], "household_role": role,
            "external_id": "", "created_at": "2024-01-15T10:00:00Z", "updated_at": "2026-04-01T09:00:00Z",
        }
        if p["membership_status"] == "Member":
            p["membership_status_date"] = dt.date(rng.randint(1990, 2024), rng.randint(1, 12), 1).strftime("%m/%d/%Y")
        if role == "Adult" and rng.random() < 0.35:
            p["external_id"] = str(1000 + n)  # envelope number
        if first in ("William", "Robert", "Elizabeth", "Margaret") and rng.random() < 0.5:
            p["nickname"] = {"William": "Bill", "Robert": "Bob", "Elizabeth": "Beth", "Margaret": "Peggy"}[first]
        people.append(p)
        return p

    def new_household(last):
        n = len(households) + 1
        street, zipc = rng.choice(STREETS)
        hh = {"household_id": sid("hh_", "stmarks", n), "household_name": f"{last} Household",
              "street": f"{rng.randint(100, 9999)} {street}",
              "street_2": rng.choice(["", "", "", "", f"Apt {rng.randint(1, 30)}"]), "zip": zipc}
        households.append(hh)
        return hh

    def pick_last():
        last = rng.choice(LAST)
        while last in used_names:
            last = rng.choice(LAST)
        used_names.add(last)
        return last

    def dob(lo, hi):
        return dt.date(rng.randint(lo, hi), rng.randint(1, 12), rng.randint(1, 28))

    for _ in range(18):  # married couples, some with kids
        last = pick_last()
        hh = new_household(last)
        new_person(rng.choice(FIRST_MALE), last, "Male", dob(1950, 1990), hh, "Adult", "Married")
        new_person(rng.choice(FIRST_FEMALE), last, "Female", dob(1952, 1992), hh, "Adult", "Married")
        for _ in range(rng.choice([0, 0, 1, 2, 3])):
            if rng.random() < 0.5:
                new_person(rng.choice(KIDS_M), last, "Male", dob(2009, 2022), hh, "Child", "Single")
            else:
                new_person(rng.choice(KIDS_F), last, "Female", dob(2009, 2022), hh, "Child", "Single")
    for _ in range(4):  # single parents
        last = pick_last()
        hh = new_household(last)
        new_person(rng.choice(FIRST_FEMALE), last, "Female", dob(1975, 1992), hh, "Adult", "Divorced")
        new_person(rng.choice(KIDS_M + KIDS_F), last, "Unknown", dob(2010, 2020), hh, "Child", "Single")
    for _ in range(20):  # singles; Subsplash has no household for them
        last = pick_last() if len(used_names) < len(LAST) else rng.choice(LAST)
        street, zipc = rng.choice(STREETS)
        solo = {"household_id": "", "household_name": "", "street": f"{rng.randint(100, 9999)} {street}",
                "street_2": "", "zip": zipc}
        g = rng.choice(["Male", "Female"])
        new_person(rng.choice(FIRST_MALE if g == "Male" else FIRST_FEMALE), last, g, dob(1940, 2003), solo,
                   "Adult", rng.choice(["Single", "Single", "Widowed"]))
    return people


def apply_batch2_changes(people, rng):
    """Changes between the first and second export: moves, new emails, a new household."""
    adults = [p for p in people if p["household_role"] == "Adult"]
    changes = []
    mover = adults[3]
    old = mover["street"]
    for q in people:
        if q["household_id"] and q["household_id"] == mover["household_id"]:
            q["street"], q["street_2"], q["postal_code"] = "4129 Ortega Blvd", "", "32210"
            q["updated_at"] = "2026-11-02T14:00:00Z"
            changes.append({"profile_id": q["profile_id"], "field": "address", "old": old, "new": q["street"]})
    single = [p for p in adults if not p["household_id"]][2]
    changes.append({"profile_id": single["profile_id"], "field": "address", "old": single["street"],
                    "new": "77 Lomax St"})
    single["street"], single["postal_code"], single["updated_at"] = "77 Lomax St", "32204", "2026-11-03T08:00:00Z"
    for p in (adults[10], adults[25]):
        old = p["email"]
        p["email"] = f"{p['first_name']}.{p['last_name']}.new@example.com".lower()
        p["updated_at"] = "2026-11-05T16:30:00Z"
        changes.append({"profile_id": p["profile_id"], "field": "email", "old": old, "new": p["email"]})
    hh = {"household_id": sid("hh_", "stmarks", "new1"), "household_name": "Wexford Household"}
    for first, g, role, year in (("Nathan", "Male", "Adult", 1988), ("Olivia", "Female", "Adult", 1990),
                                 ("Theo", "Male", "Child", 2021)):
        n = len(people) + 1
        people.append({
            "profile_id": sid("prf_", "stmarks", n), "first_name": first, "last_name": "Wexford", "nickname": "",
            "email": f"{first}.wexford@example.com".lower() if role == "Adult" else "",
            "phone": f"904-555-0{190 + n % 10}" if role == "Adult" else "", "gender": g,
            "date_of_birth": dt.date(year, 3, 14).strftime("%m/%d/%Y"),
            "marital_status": "Married" if role == "Adult" else "Single", "membership_status": "Visitor",
            "membership_status_date": "", "street": "1530 Challen Ave", "street_2": "", "city": "Jacksonville",
            "state": "FL", "postal_code": "32205", "country": "US", "household_id": hh["household_id"],
            "household_name": hh["household_name"], "household_role": role, "external_id": "",
            "created_at": "2026-11-08T12:00:00Z", "updated_at": "2026-11-08T12:00:00Z"})
        changes.append({"profile_id": people[-1]["profile_id"], "field": "new person", "old": "",
                        "new": f"{first} Wexford"})
    return changes


def build_groups(people, batch):
    adults = [p for p in people if p["household_role"] == "Adult"]
    kids = [p for p in people if p["household_role"] == "Child"]
    groups = [
        {"group_id": "grp_vestry", "name": "Vestry", "description": "Elected parish leadership",
         "group_type": "Leadership Team", "meeting_schedule": "Third Tuesday 7:00 PM", "is_public": "false",
         "is_active": "true"},
        {"group_id": "grp_choir", "name": "Parish Choir", "description": "Sings at the 10:30 service",
         "group_type": "Ministry Team", "meeting_schedule": "Thursday 7:00 PM", "is_public": "true",
         "is_active": "true"},
        {"group_id": "grp_biblestudy", "name": "Wednesday Bible Study", "description": "Lectionary study",
         "group_type": "Small Group", "meeting_schedule": "Wednesday 6:30 PM", "is_public": "true",
         "is_active": "true"},
        {"group_id": "grp_youth", "name": "EYC Youth Group", "description": "Episcopal Youth Community",
         "group_type": "Small Group", "meeting_schedule": "Sunday 5:00 PM", "is_public": "true",
         "is_active": "true"},
    ]
    r = random.Random(SEED + 7)  # independent of batch so memberships stay stable
    original_adults = [p for p in adults if p["last_name"] != "Wexford"]
    members = []
    for i, p in enumerate(r.sample(original_adults, 12)):
        role = "Senior Warden" if i == 0 else "Junior Warden" if i == 1 else "Member"
        members.append({"group_id": "grp_vestry", "profile_id": p["profile_id"], "role": role,
                        "joined_at": "01/15/2026"})
    for i, p in enumerate(r.sample(original_adults, 15)):
        members.append({"group_id": "grp_choir", "profile_id": p["profile_id"],
                        "role": "Leader" if i == 0 else "Member", "joined_at": "09/01/2025"})
    for i, p in enumerate(r.sample(original_adults, 10)):
        members.append({"group_id": "grp_biblestudy", "profile_id": p["profile_id"],
                        "role": "Leader" if i == 0 else "Member", "joined_at": "02/01/2026"})
    for p in kids:
        if p["date_of_birth"] and int(p["date_of_birth"][-4:]) <= 2013:
            members.append({"group_id": "grp_youth", "profile_id": p["profile_id"], "role": "Member",
                            "joined_at": "08/20/2025"})
    if batch == 2:  # the new household's adult joins Bible study
        newcomer = next(p for p in people if p["last_name"] == "Wexford" and p["first_name"] == "Nathan")
        members.append({"group_id": "grp_biblestudy", "profile_id": newcomer["profile_id"], "role": "Member",
                        "joined_at": "11/11/2026"})
    return groups, members


def build_giving(people, start, end):
    r = random.Random(SEED + 11)
    rows = []
    original = [p for p in people if p["household_role"] == "Adult" and p["last_name"] != "Wexford"]
    donors, seen = [], set()
    for p in original:  # one donor per household, fixed regardless of batch
        key = p["household_id"] or p["profile_id"]
        if key in seen or r.random() < 0.3:
            continue
        seen.add(key)
        donors.append({"p": p, "weekly": r.random() < 0.55,
                       "amount": r.choice([25, 40, 50, 75, 100, 150, 200, 250]),
                       "method": r.choice(CARD_METHODS), "source": r.choice(SOURCES),
                       "building": r.random() < 0.25, "outreach": r.random() < 0.2})
    if start >= B2_START:  # the new household starts giving in batch 2
        donors.append({"p": next(p for p in people if p["first_name"] == "Nathan" and p["last_name"] == "Wexford"),
                       "weekly": True, "amount": 60, "method": "Visa", "source": "App", "building": False,
                       "outreach": False})

    def transfer_for(day):
        return "tfr_" + (day + dt.timedelta(days=1)).strftime("%Y%m%d")  # deposited the next Monday

    def add(day, donor, fund, amount, method, source, freq, memo="", status="Succeeded", txid=None,
            transfer=None, check="", minute=None):
        fee = 0.0 if method in ("Cash", "Check") else round(amount * (0.008 if method == "ACH" else 0.029)
                                                             + (0 if method == "ACH" else 0.30), 2)
        who = donor["profile_id"] if donor else "anonymous"
        rows.append({
            "Transaction ID": txid or sid("txn_", day, who, fund[1], amount, memo),
            "Transfer ID": transfer_for(day) if transfer is None else transfer,
            "Transaction Date": f"{day.strftime('%m/%d/%Y')} 09:{minute if minute is not None else 15:02d} AM",
            "Status": status,
            "Donor Profile ID": donor["profile_id"] if donor else "",
            "Donor First Name": donor["first_name"] if donor else "",
            "Donor Last Name": donor["last_name"] if donor else "",
            "Donor Email": donor["email"] if donor else "",
            "Organization Member ID": donor["external_id"] if donor else "",
            "Fund": fund[0], "Fund Code": fund[1], "Gross Amount": f"{amount:.2f}", "Fee Amount": f"{fee:.2f}",
            "Net Amount": f"{amount - fee:.2f}", "Payment Method": method, "Check Number": check,
            "Frequency": freq, "Source": source, "Campus": "St. Mark's Episcopal Church", "Memo": memo})

    for day in sundays(start, end):
        first_sunday = day.day <= 7
        for d in donors:
            p = d["p"]
            if d["weekly"] or first_sunday:
                add(day, p, FUNDS[0], d["amount"], d["method"], d["source"], "Weekly" if d["weekly"] else "Monthly")
            if first_sunday and d["building"]:
                add(day, p, FUNDS[1], 50, d["method"], d["source"], "Monthly")
            if first_sunday and d["outreach"]:
                add(day, p, FUNDS[2], 20, d["method"], d["source"], "Monthly")
        for svc in ("8:00 AM", "10:30 AM"):  # anonymous loose plate cash, no donor
            amt = float(random.Random(f"{SEED}|plate|{day}|{svc}").randint(90, 260))
            add(day, None, FUNDS[0], amt, "Cash", "Kiosk", "One-time", memo=f"Loose plate {svc}", transfer="")
        if first_sunday:  # a check split across two funds = one transaction, two rows
            cp = donors[day.month % len(donors)]["p"]
            txid = sid("txn_", day, cp["profile_id"], "check")
            chk = str(4000 + day.month * 10 + day.day)
            add(day, cp, FUNDS[0], 300.0, "Check", "Kiosk", "One-time", txid=txid, transfer="", check=chk)
            add(day, cp, FUNDS[2], 100.0, "Check", "Kiosk", "One-time", txid=txid, transfer="", check=chk)
    # Rows Subsplash includes that must not be imported as gifts.
    if start <= dt.date(2026, 7, 6) <= end:
        add(dt.date(2026, 7, 6), None, FUNDS[0], 12.34, "Other", "Web", "One-time", status="Balance Adjustment",
            memo="GrowCurve rebate")
        add(dt.date(2026, 7, 12), donors[1]["p"], FUNDS[0], 500.0, "Visa", "Web", "One-time", status="Refunded")
    if start <= dt.date(2026, 11, 15) <= end:
        add(dt.date(2026, 11, 15), donors[2]["p"], FUNDS[1], 1000.0, "ACH", "Web", "One-time", status="Pending",
            transfer="")
    return rows


def build_attendance(people, members, start, end):
    r = random.Random(SEED + 23)
    rows = []
    pref = {p["profile_id"]: r.choice(["8:00 AM", "10:30 AM", "10:30 AM"]) for p in people}
    rate = {p["profile_id"]: r.choice([0.2, 0.5, 0.7, 0.9]) for p in people}
    for day in sundays(start, end):
        for p in people:
            rr = random.Random(f"{SEED}|{p['profile_id']}|{day}")
            if p["created_at"][:10] > day.isoformat() or rr.random() > rate[p["profile_id"]]:
                continue
            svc = pref[p["profile_id"]]
            hour, minute = (8, 0) if svc == "8:00 AM" else (10, 30)
            t = dt.datetime.combine(day, dt.time(hour, minute)) - dt.timedelta(minutes=rr.randint(2, 15))
            rows.append({"checkin_id": sid("chk_", p["profile_id"], day, svc), "profile_id": p["profile_id"],
                         "group_id": "", "event_name": f"Sunday {svc} Holy Eucharist", "location": "Nave",
                         "checkin_time": t.strftime("%m/%d/%Y %I:%M %p"),
                         "checkout_time": (t + dt.timedelta(minutes=75)).strftime("%m/%d/%Y %I:%M %p")})
        wed = day + dt.timedelta(days=3)
        if start <= wed <= end:
            for m in (m for m in members if m["group_id"] == "grp_biblestudy"):
                if random.Random(f"{SEED}|bs|{m['profile_id']}|{wed}").random() < 0.7:
                    t = dt.datetime.combine(wed, dt.time(18, 25))
                    rows.append({"checkin_id": sid("chk_", m["profile_id"], wed, "bs"),
                                 "profile_id": m["profile_id"], "group_id": "grp_biblestudy",
                                 "event_name": "Wednesday Bible Study", "location": "Parish Hall",
                                 "checkin_time": t.strftime("%m/%d/%Y %I:%M %p"),
                                 "checkout_time": (t + dt.timedelta(minutes=90)).strftime("%m/%d/%Y %I:%M %p")})
    return rows


def write_csv(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="Folder to write the Subsplash-shaped CSVs to")
    ap.add_argument("--batch", type=int, choices=[1, 2], default=1)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    people = build_people(random.Random(SEED))
    changes = apply_batch2_changes(people, random.Random(SEED + 3)) if args.batch == 2 else []
    groups, members = build_groups(people, args.batch)
    start, end = (B1_START, B1_END) if args.batch == 1 else (B2_START, B2_END)
    giving = build_giving(people, start, end)
    attendance = build_attendance(people, members, start, end)

    write_csv(os.path.join(args.out, "people.csv"), PEOPLE_COLUMNS, people)
    write_csv(os.path.join(args.out, "giving.csv"), GIVING_COLUMNS, giving)
    write_csv(os.path.join(args.out, "groups.csv"), GROUP_COLUMNS, groups)
    write_csv(os.path.join(args.out, "group_members.csv"), GROUP_MEMBER_COLUMNS, members)
    write_csv(os.path.join(args.out, "attendance.csv"), ATTENDANCE_COLUMNS, attendance)
    with open(os.path.join(args.out, "changes.json"), "w", encoding="utf-8") as f:
        json.dump({"batch": args.batch, "window": [start.isoformat(), end.isoformat()], "changes": changes}, f,
                  indent=2)
    print(f"batch {args.batch}: {len(people)} people, {len(giving)} giving rows, {len(groups)} groups, "
          f"{len(members)} group members, {len(attendance)} attendance rows -> {args.out}")


if __name__ == "__main__":
    main()
