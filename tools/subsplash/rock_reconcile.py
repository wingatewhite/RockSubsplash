r"""Reconcile Rock with the Subsplash ledger after a Bulk Import, and retire moved-from addresses.

Run after importing a .slingshot through Admin Tools > Power Tools > Bulk Import. The Bulk Import
StartImport block action cannot be called from a script in this Rock version: it joins a RealTime
(SignalR) channel using the browser's connection id and aborts with TaskCanceledException when
the id is not a live connection, so the import itself stays a manual UI step (see RUNBOOK.md).

What this does, using only Rock's REST API (signed in through /api/Auth/Login):
  * compares Subsplash-keyed people, transactions, details, attendance and per-fund totals with the
    cumulative ledger written by subsplash_to_slingshot.py, and flags duplicate people;
  * with --retire-moved-addresses, changes a family's old Home address to Previous when the latest
    Subsplash export has a different address. Bulk Import adds the new address but never removes
    the old one, so without this step a family that moved ends up with two Home addresses.

The password is read from the ROCK_PASSWORD environment variable.

Usage:
    set ROCK_PASSWORD=...
    python rock_reconcile.py --ledger C:\RockSync\subsplash\state\subsplash-ledger.json --retire-moved-addresses
"""

import argparse
import collections
import decimal
import http.cookiejar
import json
import os
import sys
import urllib.parse
import urllib.request

FOREIGN_SYSTEM_KEY = "Subsplash"
GROUP_TYPE_FAMILY_ID = 10
GROUP_LOCATION_TYPE_HOME = "8C52E53C-2A66-435A-AE6E-5EE307D9A0DC"
GROUP_LOCATION_TYPE_PREVIOUS = "853D98F1-6E08-4321-861B-520B4106CFE0"


class Rock:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        with self.opener.open(req, timeout=600) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text) if text.strip() else None

    def login(self, username, password):
        self.request("POST", "/api/Auth/Login", {"Username": username, "Password": password, "Persisted": False})

    def query(self, entity, flt, select):
        return self.request("GET", f"/api/{entity}?" + urllib.parse.urlencode({"$filter": flt, "$select": select})) or []

    def odata(self, entity, select, extra_filter=""):
        flt = f"ForeignKey eq '{FOREIGN_SYSTEM_KEY}'" + (f" and {extra_filter}" if extra_filter else "")
        return self.query(entity, flt, select)

    def defined_value_id(self, guid):
        return self.query("DefinedValues", f"Guid eq guid'{guid}'", "Id")[0]["Id"]


def same_address(location, expected):
    def norm(v):
        return " ".join((v or "").lower().replace(".", "").split())
    return (norm(location.get("Street1")) == norm(expected["Street1"])
            and norm(location.get("PostalCode"))[:5] == norm(expected["PostalCode"])[:5])


def retire_moved_addresses(rock, ledger):
    home = rock.defined_value_id(GROUP_LOCATION_TYPE_HOME)
    previous = rock.defined_value_id(GROUP_LOCATION_TYPE_PREVIOUS)
    families = rock.odata("Groups", "Id,ForeignId", f"GroupTypeId eq {GROUP_TYPE_FAMILY_ID}")
    retired = []
    for family in families:
        expected = ledger.get("family_addresses", {}).get(str(family["ForeignId"]))
        if not expected:
            continue
        homes = rock.query("GroupLocations", f"GroupId eq {family['Id']} and GroupLocationTypeValueId eq {home}",
                           "Id,LocationId")
        if len(homes) < 2:
            continue
        located = [(gl, rock.request("GET", f"/api/Locations/{gl['LocationId']}")) for gl in homes]
        if not any(same_address(loc, expected) for _, loc in located):
            continue  # never retire every Home address; leave it for a person to review
        for gl, loc in located:
            if not same_address(loc, expected):
                rock.request("PATCH", f"/api/GroupLocations/{gl['Id']}",
                             {"GroupLocationTypeValueId": previous, "IsMailingLocation": False,
                              "IsMappedLocation": False})
                retired.append(f"family {family['Id']}: {loc.get('Street1')} {loc.get('PostalCode')} -> Previous")
    print(json.dumps({"retired_addresses": retired}, indent=2))
    return retired


def reconcile(rock, ledger):
    people = rock.odata("People", "Id,ForeignId")
    by_foreign = collections.Counter(p["ForeignId"] for p in people)
    duplicates = {k: v for k, v in by_foreign.items() if v > 1}
    accounts = {a["Id"]: a["Name"] for a in rock.odata("FinancialAccounts", "Id,Name")}
    details = rock.odata("FinancialTransactionDetails", "Id,Amount,AccountId")
    rock_totals = collections.defaultdict(decimal.Decimal)
    for d in details:
        rock_totals[accounts.get(d["AccountId"], f"Account {d['AccountId']}")] += decimal.Decimal(str(d["Amount"]))
    expected_totals = collections.defaultdict(decimal.Decimal)
    for fund, amount in ledger["details"].values():
        expected_totals[fund] += decimal.Decimal(amount)
    result = {
        "people": {"rock": len(people), "expected": len(ledger["people"]), "duplicate_foreign_ids": duplicates},
        "financial_transactions": {"rock": len(rock.odata("FinancialTransactions", "Id")),
                                   "expected": len(ledger["transactions"])},
        "financial_transaction_details": {"rock": len(details), "expected": len(ledger["details"])},
        "attendance": {"rock": len(rock.odata("Attendances", "Id")), "expected": len(ledger["attendance"])},
        "fund_totals": {fund: {"rock": f"{rock_totals.get(fund, 0):.2f}", "expected": f"{total:.2f}"}
                        for fund, total in sorted(expected_totals.items())},
    }
    ok = (not duplicates and all(v["rock"] == v["expected"] for k, v in result.items() if k != "fund_totals")
          and all(v["rock"] == v["expected"] for v in result["fund_totals"].values()))
    result["reconciled"] = ok
    print(json.dumps(result, indent=2))
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rock-url", default="http://localhost:8080")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--ledger", required=True, help="subsplash-ledger.json written by subsplash_to_slingshot.py")
    ap.add_argument("--retire-moved-addresses", action="store_true")
    args = ap.parse_args(argv)

    password = os.environ.get("ROCK_PASSWORD")
    if not password:
        raise SystemExit("Set ROCK_PASSWORD to the Rock admin password")
    with open(args.ledger, encoding="utf-8") as f:
        ledger = json.load(f)
    rock = Rock(args.rock_url)
    rock.login(args.username, password)
    if args.retire_moved_addresses:
        retire_moved_addresses(rock, ledger)
    return 0 if reconcile(rock, ledger) else 2


if __name__ == "__main__":
    sys.exit(main())
