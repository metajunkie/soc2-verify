#!/usr/bin/env python3
"""
Build the TSC <-> CSA CCM crosswalk from AICPA's official mapping
("Mapping: 2017 Trust Services Criteria to CSA's Cloud Controls Matrix", file 'TSC to CSA CCM.xls'),
and reconcile it against your current CCM release (CCMv4.1.x spreadsheet).

Writes soc2v2/criteria/ccm_crosswalk.json (IDs, titles, mappings; no CCM specification text).
AICPA and CSA content: internal use only, check CSA's license terms before commercial use.

Usage:
  python3 soc2v2/criteria/build_ccm_crosswalk.py ["TSC to CSA CCM.xls"] ["CCM+CAIQv4.1 Bundle/CCMv4.1.1-....xlsx"]
"""
import glob
import json
import os
import re
import shutil
import sys
import tempfile

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MAP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "TSC to CSA CCM.xls")
CCM = sys.argv[2] if len(sys.argv) > 2 else (sorted(glob.glob(os.path.join(ROOT, "CCM*", "CCMv4*.xlsx"))) or [None])[-1]
OUT = os.path.join(HERE, "ccm_crosswalk.json")
TSC_RE = re.compile(r"^(CC\d\.\d|PI1\.\d|A1\.\d|C1\.\d|P\d\.\d)$")
CCM_RE = re.compile(r"^[A-Z&]{3}-\d{2}$")
ID_FIND = re.compile(r"[A-Z&]{3}-\d{2}")


def open_xlsx(path):
    # AICPA ships an .xlsx file with an .xls extension; openpyxl needs the right suffix
    if path.lower().endswith(".xls"):
        tmp = os.path.join(tempfile.mkdtemp(), "map.xlsx")
        shutil.copy(path, tmp)
        path = tmp
    return openpyxl.load_workbook(path, data_only=True, read_only=True)


def ccm_to_tsc_ids(tsc_to_ccm):
    return {c for ids in tsc_to_ccm.values() for c in ids}


def main():
    wb = open_xlsx(MAP)
    version = {}
    for r in wb["Overview"].iter_rows(values_only=True):
        if r and r[0] in ("Trust Services Criteria", "CSA CCM"):
            version[r[0]] = next((str(x) for x in r[1:] if x), "")
    ws = next(w for w in wb.worksheets if "CCM" in w.title and w.title != "Overview")
    tsc_to_ccm, unmapped_tsc, map_text = {}, [], {}
    for r in ws.iter_rows(values_only=True):
        if not r or not r[0]:
            continue
        tsc, ref = str(r[0]).strip(), str(r[1] or "").strip()
        if not TSC_RE.match(tsc):
            continue
        if ref.upper() == "NONE":
            unmapped_tsc.append(tsc)
            continue
        ids = ID_FIND.findall(ref)
        if len(ids) == 1 and len(r) > 2 and r[2]:
            map_text.setdefault(ids[0], str(r[2]))
        for cid in ids:
            tsc_to_ccm.setdefault(tsc, [])
            if cid not in tsc_to_ccm[tsc]:
                tsc_to_ccm[tsc].append(cid)

    ccm_to_tsc = {}
    for tsc, ids in tsc_to_ccm.items():
        for cid in ids:
            ccm_to_tsc.setdefault(cid, []).append(tsc)

    # current CCM release: titles and domains (no specification text)
    current, domain = {}, None
    if CCM:
        cw = open_xlsx(CCM)["CCM"]
        for r in cw.iter_rows(values_only=True):
            if not r:
                continue
            if r[0] and not r[1] and not r[2] and " - " in str(r[0]):
                domain = str(r[0]).strip()
            elif r[2] and CCM_RE.match(str(r[2]).strip()):
                current[str(r[2]).strip()] = {"title": str(r[1] or "").strip(), "domain": domain,
                                              "_spec": str(r[3] or "")}

    # controls renamed between the mapped CCM version and the current one (e.g. a domain prefix change):
    # match by specification text, never by number alone
    from difflib import SequenceMatcher
    renamed = {}
    for old in sorted(set(ccm_to_tsc_ids(tsc_to_ccm)) - set(current)):
        if old not in map_text:
            continue
        best = max(((SequenceMatcher(None, map_text[old].lower(), v["_spec"].lower()).ratio(), k)
                    for k, v in current.items()), default=(0, None))
        if best[0] >= 0.8:
            renamed[old] = {"current_id": best[1], "similarity": round(best[0], 2)}
    for v in current.values():
        v.pop("_spec", None)
    for tsc, ids in tsc_to_ccm.items():
        tsc_to_ccm[tsc] = [renamed[i]["current_id"] if i in renamed else i for i in ids]

    ccm_to_tsc = {}
    for tsc, ids in tsc_to_ccm.items():
        for cid in ids:
            ccm_to_tsc.setdefault(cid, []).append(tsc)
    mapped_ids = set(ccm_to_tsc)
    out = {
        "_about": "Built from AICPA 'Mapping: 2017 Trust Services Criteria to CSA's Cloud Controls Matrix' "
                  "and CSA CCM. AICPA/CSA content: internal use only; check CSA license terms for commercial use.",
        "mapping_source": os.path.basename(MAP),
        "mapping_versions": version,
        "current_ccm_source": os.path.basename(CCM) if CCM else None,
        "tsc_to_ccm": tsc_to_ccm,
        "ccm_to_tsc": {k: sorted(v) for k, v in sorted(ccm_to_tsc.items())},
        "tsc_with_no_ccm_mapping": unmapped_tsc,
        "ccm_controls": current,
        "ccm_not_mapped_to_tsc": sorted(set(current) - mapped_ids),
        "renamed_since_mapping": renamed,
        "mapped_ids_not_in_current_ccm": sorted(mapped_ids - set(current)) if current else [],
    }
    json.dump(out, open(OUT, "w"), indent=2)
    print(f"[✓] {len(tsc_to_ccm)} TSC criteria map to {len(mapped_ids)} CCM controls "
          f"(mapping: TSC {version.get('Trust Services Criteria')}, CCM {version.get('CSA CCM')})")
    print(f"    {len(unmapped_tsc)} TSC criteria have no CCM equivalent: {', '.join(unmapped_tsc)}")
    if current:
        print(f"    Current CCM ({os.path.basename(CCM)}): {len(current)} controls; "
              f"{len(out['ccm_not_mapped_to_tsc'])} not mapped to any TSC criterion; "
              f"renamed since mapping: {len(renamed)}; "
              f"mapped IDs missing from current CCM: {out['mapped_ids_not_in_current_ccm'] or 'none'}")
    print(f"    -> {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
