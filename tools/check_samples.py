#!/usr/bin/env python3
"""
Score the code parser against the samples' ground truth, exactly.

The sample reports are generated (tools/make_samples.py), so the right answer for every control is known.
This check fails if any control is missing, extra, under the wrong criterion, has the wrong results, or
has an evidence quote that isn't on its page.

  python3 tools/check_samples.py
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from soc2v2.parse_controls import parse_best  # noqa: E402
from soc2v2.paths import report_pdf  # noqa: E402
from soc2v2.pdftext import PdfDoc  # noqa: E402
from soc2v2.verifier import _control_pages  # noqa: E402


def main():
    failed = 0
    for truth_path in sorted(glob.glob(os.path.join(HERE, "Vendors", "Sample-*", "extraction_truth.json"))):
        vdir = os.path.dirname(truth_path)
        name = os.path.basename(vdir)
        doc = PdfDoc(report_pdf(vdir))
        P = parse_best(doc, _control_pages(doc))
        truth = json.load(open(truth_path))
        want = {c["id"]: (sorted(c["criteria"]), [t["result"] for t in c["tests"]]) for c in truth["controls"]}
        got = {c["id"]: (sorted(c["criteria"]), [t["result"] for t in c["tests"]]) for c in P["controls"]}
        problems = [f"missing {k}" for k in want if k not in got]
        problems += [f"extra {k}" for k in got if k not in want]
        problems += [f"{k}: expected {want[k]}, parsed {got[k]}" for k in want if k in got and got[k] != want[k]]
        for c in P["controls"]:
            ev = c["evidence"]
            if doc.find_quote(ev["quote"], ev["pdf_page"])["status"] not in ("exact", "approximate"):
                problems.append(f"{c['id']}: quote not on p.{ev['pdf_page']}")
        gate = P["results"]["layout_gate"]["status"]
        if gate != "PASS":
            problems.append(f"layout gate {gate}")
        if problems:
            failed += 1
            print(f"[x] {name}: {len(problems)} problem(s)")
            for p in problems[:20]:
                print(f"      - {p}")
        else:
            print(f"[✓] {name}: {len(want)} of {len(want)} controls exact, all quotes verified, gate PASS "
                  f"({P['results']['layout']})")
    print(f"\n{'FAILED' if failed else 'PASSED'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
