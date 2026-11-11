#!/usr/bin/env python3
"""
soc2-verify: verify an AI-assisted SOC 2 Type II review
=====================================================

Design:  code reads, AI answers, code checks, a human decides.

  1. Scope profile (human)     Vendors/<Vendor>/scope_profile.yaml   How the client uses this vendor.
  2. Extraction (any AI)       Vendors/<Vendor>/extraction.json      Facts + verbatim quotes + pages,
                                                              per soc2v2/schema/extraction.schema.json,
                                                              using soc2v2/prompts/extraction_prompt.md.
  3. Verification (this code)  Vendors/<Vendor>/Review_<date>/v2/    Deterministic checks against the PDF.
  4. Outputs (adapters)        workbook / Jira / client report, written only from verified data.
  5. Risk decision (human)     Never automated.

Vendor folders live under Vendors/ (e.g. Vendors/ExampleCo/). --vendor takes the folder name.

Usage:
  python3 soc2_review.py verify    --vendor ExampleCo [--extraction F] [--profile F] [--date YYYY-MM-DD]
  python3 soc2_review.py prompt                                          # show the extraction prompt

Requires: pypdf, openpyxl; pyyaml for YAML profiles (pip install pyyaml), or use a .json profile.
"""

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from soc2v2 import verifier            # noqa: E402
from soc2v2.paths import vendor_dir as resolve_vendor  # noqa: E402

VERSION = "2.0.0"


def load_profile(path):
    if not path or not os.path.exists(path):
        return None
    if path.endswith(".json"):
        return json.load(open(path))
    try:
        import yaml
    except ImportError:
        sys.exit("pyyaml is needed for YAML profiles: pip install pyyaml (or save the profile as .json)")
    return yaml.safe_load(open(path))


def find_soc2_pdf(vendor_dir, hint=None):
    from soc2v2.paths import report_pdf
    try:
        return report_pdf(vendor_dir, hint)
    except FileNotFoundError as e:
        sys.exit(f"{e}; pass --pdf")


def write_disposition_template(res, path, profile):
    """Write every open FAIL/REVIEW item as a disposition stub for the reviewer to complete."""
    reviewer = (profile or {}).get("reviewer", "")
    L = ["# Reviewer dispositions: one entry per finding you have decided on.",
         "# decision: accepted | mitigated | follow_up | corrected | rejected",
         "#   corrected = the AI output or form was fixed; rejected = vendor not approved on this point",
         "# Leave decision empty for items still open. Keys must match the verification report exactly.",
         "dispositions:"]
    for i in res.items:
        if i["status"] in ("FAIL", "REVIEW") and not i.get("disposition") and i["section"] != "Dispositions":
            k = verifier.item_key(i).replace('"', "'")
            det = str(i["detail"]).replace("\n", " ")[:160]
            L += [f'  - key: "{k}"', f"    # {i['status']}: {det}", '    decision: ""', '    rationale: ""',
                  '    evidence: ""', f'    decided_by: "{reviewer}"', '    date: ""', '    revisit: ""']
    open(path, "w").write("\n".join(L) + "\n")
    print(f"[✓] Disposition template: {os.path.relpath(path, HERE)}")


def run_verify(vendor, extraction_path, profile_path, review_date, pdf_name=None, password=None, write_template=False, overlay=None):
    try:
        vendor_dir = resolve_vendor(vendor)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    extraction_path = extraction_path or os.path.join(vendor_dir, "extraction.json")
    if not os.path.exists(extraction_path):
        sys.exit(f"No extraction found at {extraction_path}.\n"
                 f"Run the AI extraction first (see: python3 {os.path.basename(__file__)} prompt).")
    profile_path = profile_path or os.path.join(vendor_dir, "scope_profile.yaml")
    pdf_path = find_soc2_pdf(vendor_dir, pdf_name)
    extraction = json.load(open(extraction_path))
    profile = load_profile(profile_path)
    crit_dir = os.path.join(HERE, "soc2v2", "criteria")
    aicpa = os.path.join(crit_dir, "tsc_2017_aicpa.json")      # built from the AICPA PDF: source of truth
    criteria = json.load(open(aicpa if os.path.exists(aicpa) else os.path.join(crit_dir, "tsc_2017.json")))
    print(f"[*] Criteria   : {'AICPA source (' + criteria.get('source_file', '') + ')' if os.path.exists(aicpa) else 'built-in ID list (run build_tsc_from_aicpa.py)'}")

    out_dir = os.path.join(vendor_dir, f"Review_{review_date}", "v2")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(extraction_path))[0]
    out_md = os.path.join(out_dir, f"Verification_{vendor}_{stem}_{review_date}.md")
    out_json = os.path.join(out_dir, f"Verification_{vendor}_{stem}_{review_date}.json")

    print(f"[*] Source PDF : {os.path.basename(pdf_path)}")
    print(f"[*] Extraction : {os.path.relpath(extraction_path, HERE)}")
    print(f"[*] Profile    : {os.path.relpath(profile_path, HERE) if profile else '(none)'}")
    doc, res = verifier.verify(pdf_path, extraction, profile, criteria, review_date, password)
    overlays = set((profile or {}).get("overlays") or []) | set(overlay or [])
    if "ccm" in overlays:
        xw = os.path.join(HERE, "soc2v2", "criteria", "ccm_crosswalk.json")
        if not os.path.exists(xw):
            sys.exit("CCM overlay requested but ccm_crosswalk.json is missing: run soc2v2/criteria/build_ccm_crosswalk.py")
        rows = verifier.ccm_overlay(doc, extraction, res, json.load(open(xw)))
        ccm_md = os.path.join(out_dir, f"CCM_Coverage_{vendor}_{stem}_{review_date}.md")
        verifier.write_ccm_appendix(rows, ccm_md, extraction.get("report", {}).get("service_org", vendor))
        print(f"[✓] CCM    : {os.path.relpath(ccm_md, HERE)}")
    disp_path = os.path.join(vendor_dir, "dispositions.yaml")
    if os.path.exists(disp_path):
        import yaml
        disp = yaml.safe_load(open(disp_path)) or {}
        res.apply_dispositions(disp.get("dispositions", []))
        print(f"[*] Dispositions: {os.path.relpath(disp_path, HERE)}")
    if write_template:
        write_disposition_template(res, disp_path if not os.path.exists(disp_path) else disp_path + ".new", profile)
    meta = verifier.write_report(doc, res, extraction, profile, out_md, out_json, extraction_path, review_date, VERSION)
    c = meta["counts"]
    print(f"[=] OVERALL: {meta['overall']}  (FAIL {c.get('FAIL',0)} / REVIEW {c.get('REVIEW',0)} / "
          f"PASS {c.get('PASS',0)} / INFO {c.get('INFO',0)})")
    print(f"[✓] Report : {os.path.relpath(out_md, HERE)}")
    print(f"[✓] JSON   : {os.path.relpath(out_json, HERE)}")
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("verify",):
        p = sub.add_parser(name)
        p.add_argument("--vendor", required=True)
        p.add_argument("--extraction")
        p.add_argument("--profile")
        p.add_argument("--pdf", help="SOC report file name inside the vendor folder")
        p.add_argument("--date", default=dt.date.today().isoformat())
        p.add_argument("--password", help="PDF open password, if the vendor supplied one")
        p.add_argument("--dispositions-template", action="store_true", help="write a dispositions.yaml stub for open items")
        p.add_argument("--overlay", action="append", choices=["ccm"], help="also report coverage in another framework's terms")
    sub.add_parser("prompt")
    a = ap.parse_args()

    if a.cmd == "prompt":
        print(open(os.path.join(HERE, "soc2v2", "prompts", "extraction_prompt.md")).read())
        return
    review_date = dt.date.fromisoformat(a.date)
    run_verify(a.vendor, a.extraction, a.profile, review_date, a.pdf, a.password, a.dispositions_template, a.overlay)


if __name__ == "__main__":
    main()
