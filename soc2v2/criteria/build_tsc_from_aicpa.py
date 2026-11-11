#!/usr/bin/env python3
"""
Build the criteria library from YOUR licensed copy of the AICPA document:
  "2017 Trust Services Criteria (With Revised Points of Focus - 2022)"  (Trust-services-criteria.pdf)

The AICPA PDF is the source of truth. This script parses it locally and writes
soc2v2/criteria/tsc_2017_aicpa.json (criterion text + points of focus). The verifier prefers that
file over the hand-made ID list. Both the PDF and the generated JSON are AICPA copyrighted
content: keep them for internal use, don't redistribute them.

Usage:  python3 soc2v2/criteria/build_tsc_from_aicpa.py [path/to/Trust-services-criteria.pdf]
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys

import pypdf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "Trust-services-criteria.pdf")
OUT = os.path.join(HERE, "tsc_2017_aicpa.json")

CAT = [("CC", "Security"), ("PI", "Processing Integrity"), ("A", "Availability"), ("C", "Confidentiality"), ("P", "Privacy")]
ID = re.compile(r"^\s*(CC\d\.\d|PI1\.\d|A1\.\d|C1\.\d|P\d\.\d)\s+(.*)$")
NOISE = [re.compile(p) for p in (r"^\s*Page \d+\s*$", r"^\s*TSP\s*$", r"^\s*Ref\. #\s*$",
                                 r"^\s*TRUST SERVICES CRITERIA AND POINTS OF FOCUS\s*$", r"^\s*$")]
POF_INTRO = re.compile(r"The following points of focus|Points of focus specified|Additional point[s]? of focus")


def category(cid):
    for pref, name in CAT:
        if cid.startswith(pref) and not (pref == "C" and cid.startswith("CC")) and not (pref == "P" and cid.startswith("PI")):
            return name


def clean(s):
    s = re.sub(r"-\s*\n\s*", "", s)          # re-join hyphenated line breaks ("integ-\nrity")
    return re.sub(r"\s+", " ", s).strip()


def main():
    raw = open(SRC, "rb").read()
    reader = pypdf.PdfReader(SRC)
    lines = []
    for pno, page in enumerate(reader.pages, 1):
        for ln in (page.extract_text() or "").splitlines():
            if not any(p.match(ln) for p in NOISE):
                lines.append((pno, ln))

    # the criteria tables start at the first "CC1.1" line followed by COSO text
    start = next(i for i, (_, ln) in enumerate(lines) if re.match(r"^\s*CC1\.1\s+COSO Principle 1", ln))
    blocks, cur = [], None
    for pno, ln in lines[start:]:
        m = ID.match(ln)
        if m:
            cur = {"id": m.group(1), "page": pno, "lines": [m.group(2)]}
            blocks.append(cur)
        elif cur:
            cur["lines"].append(ln)

    criteria, groups = {}, {}
    for b in blocks:
        text = "\n".join(b["lines"])
        if b["id"].endswith(".0"):                       # Privacy section headers (P1.0 ... P8.0)
            groups[b["id"]] = clean(re.split(r"\n\s*\n|•", text)[0])
            continue
        if b["id"] in criteria:                          # repeated reference (e.g. in a later table); keep first
            continue
        m = POF_INTRO.search(text)
        crit_text = clean(text[:m.start()] if m else text.split("•")[0])
        pofs = []
        for part in text.split("•")[1:]:
            part = POF_INTRO.split(part)[0]
            p = clean(part)
            title, _, desc = p.partition(" — ")
            if title:
                pofs.append({"title": title.strip(), "description": desc.strip()})
        criteria[b["id"]] = {"category": category(b["id"]), "text": crit_text, "points_of_focus": pofs,
                             "source_page": b["page"]}

    out = {
        "_about": "Parsed from the user's licensed AICPA '2017 Trust Services Criteria (With Revised Points of Focus - 2022)'. "
                  "AICPA copyrighted content: internal use only; do not redistribute.",
        "source_file": os.path.basename(SRC),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "categories": {"Security": "CC", "Availability": "A", "Processing Integrity": "PI", "Confidentiality": "C", "Privacy": "P"},
        "privacy_groups": groups,
        "criteria": criteria,
    }
    json.dump(out, open(OUT, "w"), indent=2, ensure_ascii=False)
    by_cat = {}
    for cid, c in criteria.items():
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    print(f"[✓] {len(criteria)} criteria ({by_cat}), {sum(len(c['points_of_focus']) for c in criteria.values())} points of focus, "
          f"{len(groups)} privacy groups -> {os.path.relpath(OUT, ROOT)}")

    # cross-check against the hand-made ID list the tools shipped with
    legacy = os.path.join(HERE, "tsc_2017.json")
    if os.path.exists(legacy):
        old = set(json.load(open(legacy))["criteria"])
        new = set(criteria)
        print(f"[=] vs. tsc_2017.json: missing from AICPA parse {sorted(old - new) or 'none'}; "
              f"new in AICPA {sorted(new - old) or 'none'}")


if __name__ == "__main__":
    main()
