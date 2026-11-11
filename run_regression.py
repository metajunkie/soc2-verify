#!/usr/bin/env python3
"""
Regression tests for the parser and verifier.

Each signed-off report is a locked test case. For each case this re-runs the code parser and the verifier
(in memory; nothing is written to the vendor folders) and compares the results with the stored snapshot:

  - the parser: layout chosen, every control ID with its criteria and test results, the layout gate
  - the verifier: the status of every check, run against a pinned extraction file and review date

Any difference fails the run. Run it after every change to soc2v2/ and before using the change on a real review.

  python3 run_regression.py                         # check everything against the snapshots
  python3 run_regression.py --update --reason "..." # accept current behaviour as the new snapshot (logged)

A snapshot only proves the code still does what it did when the case was locked. Whether that was right is
the human sign-off recorded in tests/regression/cases.json (see PARSER_METHOD.md).
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from soc2v2 import verifier  # noqa: E402
from soc2v2.parse_controls import PARSER_VERSION, parse_best  # noqa: E402
from soc2v2.paths import report_pdf, vendor_dir  # noqa: E402
from soc2v2.pdftext import PdfDoc  # noqa: E402
from soc2v2.verifier import _control_pages  # noqa: E402

REG = os.path.join(HERE, "tests", "regression")
CASES = os.path.join(REG, "cases.json")

_spec = importlib.util.spec_from_file_location("cli", os.path.join(HERE, "soc2_review.py"))
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snapshot(case):
    vdir = vendor_dir(case["vendor"])
    pdf = report_pdf(vdir, case.get("pdf"))
    doc = PdfDoc(pdf)
    P = parse_best(doc, _control_pages(doc))
    parser = {
        "layout": P["results"].get("layout"),
        "counts": {k: P["results"][k] for k in ("controls", "tests", "clean", "exceptions", "independent_clean_count")},
        "layout_gate": P["results"].get("layout_gate", {}).get("status"),
        "controls": {c["id"]: {"criteria": sorted(c["criteria"]), "results": [t["result"] for t in c["tests"]],
                               "quote_page": c["evidence"]["pdf_page"]} for c in P["controls"]},
    }
    ex_path = os.path.join(vdir, case["extraction"])
    extraction = json.load(open(ex_path))
    profile = cli.load_profile(os.path.join(vdir, "scope_profile.yaml"))
    crit_dir = os.path.join(HERE, "soc2v2", "criteria")
    aicpa = os.path.join(crit_dir, "tsc_2017_aicpa.json")
    criteria = json.load(open(aicpa if os.path.exists(aicpa) else os.path.join(crit_dir, "tsc_2017.json")))
    _, res = verifier.verify(pdf, extraction, profile, criteria, dt.date.fromisoformat(case["review_date"]))
    checks = {}
    for it in res.items:
        k = f"{it['section']} :: {it['check']}"
        checks[k] = checks.get(k, "") + ("," if k in checks else "") + it["status"]
    return {
        "case": case["name"], "source_pdf": os.path.basename(pdf), "source_pdf_sha256": doc.sha256,
        "extraction": case["extraction"], "extraction_sha256": sha256(ex_path),
        "review_date": case["review_date"], "parser_version": PARSER_VERSION,
        "verifier_version": cli.VERSION, "overall": res.overall(), "counts": res.counts(),
        "parser": parser, "verifier_checks": checks,
    }


def diff(old, new):
    out = []
    for k in ("source_pdf_sha256", "extraction_sha256"):
        if old[k] != new[k]:
            out.append(f"input changed: {k} (re-lock the case on purpose if this is expected)")
    po, pn = old["parser"], new["parser"]
    if po["layout"] != pn["layout"]:
        out.append(f"parser layout: {po['layout']} -> {pn['layout']}")
    for k in po["counts"]:
        if po["counts"][k] != pn["counts"].get(k):
            out.append(f"parser {k}: {po['counts'][k]} -> {pn['counts'].get(k)}")
    if po["layout_gate"] != pn["layout_gate"]:
        out.append(f"layout gate: {po['layout_gate']} -> {pn['layout_gate']}")
    co, cn = po["controls"], pn["controls"]
    for cid in sorted(set(co) - set(cn)):
        out.append(f"control {cid}: no longer parsed")
    for cid in sorted(set(cn) - set(co)):
        out.append(f"control {cid}: newly parsed")
    for cid in sorted(set(co) & set(cn)):
        if co[cid] != cn[cid]:
            out.append(f"control {cid}: {co[cid]} -> {cn[cid]}")
    vo, vn = old["verifier_checks"], new["verifier_checks"]
    for k in sorted(set(vo) | set(vn)):
        if vo.get(k) != vn.get(k):
            out.append(f"verifier '{k}': {vo.get(k, '(absent)')} -> {vn.get(k, '(absent)')}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="store current results as the new snapshots")
    ap.add_argument("--reason", help="required with --update; recorded in tests/regression/UPDATES.md")
    ap.add_argument("--case", help="run only this case name")
    a = ap.parse_args()
    if a.update and not a.reason:
        sys.exit("--update needs --reason: say why the new behaviour is correct.")
    cases = json.load(open(CASES))["cases"]
    failed = 0
    for case in cases:
        if a.case and case["name"] != a.case:
            continue
        snap_path = os.path.join(REG, f"{case['name']}.json")
        try:
            new = snapshot(case)
        except Exception as e:  # noqa: BLE001
            print(f"[x] {case['name']}: could not run ({e})")
            failed += 1
            continue
        signed = case.get("signed_off_by")
        tag = f"signed off by {signed} on {case.get('signed_off_on')}" if signed else "NOT YET SIGNED OFF"
        if a.update or not os.path.exists(snap_path):
            json.dump(new, open(snap_path, "w"), indent=1, sort_keys=True)
            print(f"[=] {case['name']}: snapshot {'updated' if a.update else 'created'} "
                  f"({new['parser']['counts']['controls']} controls, verifier {new['overall']}) [{tag}]")
            continue
        old = json.load(open(snap_path))
        d = diff(old, new)
        if d:
            failed += 1
            print(f"[x] {case['name']}: {len(d)} difference(s) [{tag}]")
            for line in d[:40]:
                print(f"      - {line}")
            if len(d) > 40:
                print(f"      ... and {len(d) - 40} more")
        else:
            print(f"[✓] {case['name']}: matches snapshot ({new['parser']['counts']['controls']} controls, "
                  f"verifier {new['overall']}) [{tag}]")
    if a.update:
        with open(os.path.join(REG, "UPDATES.md"), "a") as f:
            f.write(f"- {dt.datetime.now().isoformat(timespec='minutes')} parser {PARSER_VERSION}, verifier "
                    f"{cli.VERSION}{' (' + a.case + ')' if a.case else ''}: {a.reason}\n")
    print(f"\n{'FAILED' if failed else 'PASSED'}: {failed} case(s) with differences.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
