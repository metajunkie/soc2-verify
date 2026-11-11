#!/usr/bin/env python3
"""
Convert a completed CyberCede Vendor Use Profile (.xlsx) into scope_profile.yaml for the SOC 2 verifier.

  python3 profile_from_xlsx.py Vendors/ExampleCo/Vendor_Use_Profile_ExampleCo.xlsx    # writes Vendors/ExampleCo/scope_profile.yaml
  python3 profile_from_xlsx.py <file.xlsx> --out <path.yaml>

The tiering rules are computed here in code AND by formulas in the workbook. Thresholds, evidence and
cadence come from the workbook's Rules tab, so policy is tuned in one place. If the workbook's
calculated results (as last saved by Excel) disagree with this code, the script says so: two
implementations of one rule are a control on each other.
"""
import argparse
import datetime as dt
import hashlib
import os
import sys

import openpyxl

DATA_KEYS = {"B1": "business_contact", "B2": "pii", "B3": "sensitive_pii", "B4": "phi", "B5": "pci",
             "B6": "financial", "B7": "credentials", "B8": "source_code", "B9": "confidential_business"}
TIERS = ["Tier 1 - Critical", "Tier 2 - High", "Tier 3 - Moderate", "Tier 4 - Low"]


def read(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Vendor Use Profile"]
    ans, results = {}, {}
    for r in range(1, ws.max_row + 1):
        qid = ws.cell(r, 1).value
        if isinstance(qid, str) and len(qid) >= 2 and qid[0] in "ABCD" and qid[1:].isdigit():
            v = ws.cell(r, 3).value
            (results if qid[0] == "D" else ans)[qid] = v.strip() if isinstance(v, str) else v
    rules = {}
    rs = wb["Rules"]
    for r in range(4, 8):
        rules[rs.cell(r, 1).value] = {"bridge": rs.cell(r, 2).value, "max_age": rs.cell(r, 3).value,
                                      "evidence": rs.cell(r, 4).value, "cadence": rs.cell(r, 5).value}
    return ans, results, rules


def compute(a, rules):
    y = lambda k: a.get(k) == "Yes"
    vol = a.get("B10") or ""
    if not any(a.get(k) for k in list(DATA_KEYS) + ["B10", "C1", "C2", "C3", "C4", "C5", "C6", "C7"]):
        sys.exit("Sections B and C are empty: complete the profile first.")
    if any(y(k) for k in ("B3", "B4", "B5", "B7", "C6", "C3")) or a.get("C1") == "Critical" or vol == "Over 100K":
        tier = TIERS[0]
    elif any(y(k) for k in ("B2", "B6", "B8", "B9", "C4", "C7")) or a.get("C1") == "High" or vol == "10K-100K":
        tier = TIERS[1]
    elif y("B1") or a.get("C1") == "Medium" or vol not in ("", "None"):
        tier = TIERS[2]
    else:
        tier = TIERS[3]
    nonpublic = any(y(k) for k in ("B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9"))
    avail = a.get("C1") in ("High", "Critical") or a.get("C2") in ("Under 4 hours", "Under 24 hours")
    pi = y("C3") or y("C4")
    priv = (y("B3") or y("B4")) and y("C5")
    personal = y("B2") or y("B3") or y("B4") or vol not in ("", "None")
    required = ["Security"] + (["Confidentiality"] if nonpublic else []) + (["Availability"] if avail else []) \
        + (["Processing Integrity"] if pi else []) + (["Privacy"] if priv else [])
    recommended = (["Availability"] if a.get("C1") == "Medium" and not avail else []) \
        + (["Privacy"] if personal and not priv else [])
    rule = rules[tier]
    return {
        "tier": tier, "required": required, "recommended": recommended,
        "bridge": 0 if y("C3") else int(rule["bridge"]),
        "bridge_text": "Mandatory for any gap (ICOFR)" if y("C3") else f"Request if gap exceeds {rule['bridge']} months",
        "max_age": int(rule["max_age"]), "evidence": rule["evidence"], "cadence": rule["cadence"],
    }


def crosscheck(res, calc):
    """Compare workbook formula results (cached by Excel/LibreOffice) to this code's results."""
    if not res or res.get("D1") in (None, ""):
        return ["Workbook has no calculated results cached (open and save it in Excel to compare)."]
    issues = []
    pairs = [("D1", calc["tier"]), ("D2", ", ".join(calc["required"])), ("D3", ", ".join(calc["recommended"]) or None),
             ("D4", calc["bridge_text"]), ("D5", calc["max_age"]), ("D6", calc["evidence"]), ("D7", calc["cadence"])]
    for k, v in pairs:
        w = res.get(k)
        w = None if w == "" else w
        if str(w) != str(v) and not (w is None and v is None):
            issues.append(f"{k}: workbook says {w!r}, code says {v!r}")
    return issues


def q(s):
    s = "" if s is None else str(s)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx")
    ap.add_argument("--out")
    a = ap.parse_args()
    ans, res, rules = read(a.xlsx)
    calc = compute(ans, rules)
    issues = crosscheck(res, calc)
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.xlsx)), "scope_profile.yaml")
    services = [s.strip() for s in str(ans.get("A3") or "").splitlines() if s.strip()]
    data_types = [v for k, v in DATA_KEYS.items() if ans.get(k) == "Yes"] or ["none"]
    sha = hashlib.sha256(open(a.xlsx, "rb").read()).hexdigest()
    L = [
        f"# Generated by profile_from_xlsx.py from {os.path.basename(a.xlsx)} on {dt.date.today()}",
        f"# Source SHA-256: {sha}",
        "# Edit the workbook, not this file, then re-run the generator.",
        f"client: {q(ans.get('A1'))}",
        f"vendor: {q(ans.get('A2'))}",
        f"reviewer: {q(ans.get('A6'))}",
        f"business_owner: {q(ans.get('A5'))}",
        f"profile_date: {q(ans.get('A7'))}",
        f"description: {q(ans.get('A4'))}",
        "services_used:          # each line checked against the report's system description; 'a | b' = either",
        *[f"  - {q(s)}" for s in services],
        "data_types:",
        *[f"  - {d}" for d in data_types],
        f"personal_record_volume: {q(ans.get('B10'))}",
        f"data_flow: {q(ans.get('B11'))}",
        f"data_location: {q(ans.get('B12'))}",
        f"vendor_ai_use_of_data: {q(ans.get('B13'))}",
        f"integrations: {q(ans.get('B14'))}",
        f"criticality: {(ans.get('C1') or 'unknown').lower()}",
        f"rto: {q(ans.get('C2'))}",
        f"icofr: {str(ans.get('C3') == 'Yes').lower()}",
        f"tier: {q(calc['tier'])}",
        "required_tsc_categories:",
        *[f"  - {c}" for c in calc["required"]],
        "recommended_tsc_categories:",
        *([f"  - {c}" for c in calc["recommended"]] or ["  []"]),
        f"max_report_age_months: {calc['max_age']}",
        f"bridge_letter_after_months: {calc['bridge']}      # {calc['bridge_text']}",
        f"evidence_required: {q(calc['evidence'])}",
        f"review_cadence: {q(calc['cadence'])}",
        "overlays: []",
    ]
    L = [l.replace("recommended_tsc_categories:\n  []", "recommended_tsc_categories: []") for l in L]
    text = "\n".join(L) + "\n"
    text = text.replace("recommended_tsc_categories:\n  []\n", "recommended_tsc_categories: []\n")
    open(out, "w").write(text)
    print(f"[✓] {calc['tier']}; required {calc['required']}; recommended {calc['recommended']} -> {out}")
    for i in issues:
        print(f"[!] {i}")
    if not issues:
        print("[✓] Workbook formulas and generator agree.")


if __name__ == "__main__":
    main()
