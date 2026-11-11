#!/usr/bin/env python3
"""
Hybrid SOC 2 extraction: code parses, the AI answers targeted questions (method "C on D").

  D  Code parses the control-testing tables (soc2v2/parse_controls.py): control IDs, the criterion each
     is listed under, descriptions, test procedures and results. No AI, and every quote is verbatim.
  C  The local model (Ollama) answers only targeted questions, each with just the pages it needs:
       - fixed side-questions: report facts and opinion; subservice orgs and CUECs
       - any control whose result the parser couldn't read as a clean pass (exceptions, "unable to test")
       - criterion questions: --criteria gaps  -> only criteria the parser found no tested control for
                              --criteria all   -> every criterion in the report's categories (cross-check)
     Every answer must be a pointer (control ID + page + verbatim quote). Code checks each quote and ID
     against the PDF; a failed answer is sent back once with the errors listed. Whatever still fails is
     reported as unresolved for the reviewer, never silently kept.

Output is a normal extraction.json, so the verifier checks it exactly like any other extraction.

  python3 extract_hybrid.py --vendor ExampleCo --model gemma4:26b               # gaps (fast)
  python3 extract_hybrid.py --vendor ExampleCo --model gemma4:26b --criteria all
  python3 extract_hybrid.py --vendor ExampleCo --dry-run                         # parser only, no model
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_local import PROMPT, SIDE_ASK, STATS, call_ollama, side_pages, subschema, tagged  # noqa: E402
from soc2v2.parse_controls import parse_best  # noqa: E402
from soc2v2.paths import report_pdf, vendor_dir as resolve_vendor  # noqa: E402
from soc2v2.pdftext import PdfDoc  # noqa: E402
from soc2v2.verifier import _control_pages  # noqa: E402

PREFIX = {"Security": "CC", "Availability": "A", "Confidentiality": "C", "Processing Integrity": "PI", "Privacy": "P"}
EV = {"type": "object", "required": ["quote", "pdf_page"],
      "properties": {"quote": {"type": "string"}, "pdf_page": {"type": "integer"}}}

CRIT_SCHEMA = {
    "type": "object", "required": ["criterion", "status", "controls"],
    "properties": {
        "criterion": {"type": "string"},
        "status": {"enum": ["tested_controls", "not_applicable", "not_addressed"]},
        "controls": {"type": "array", "items": {
            "type": "object", "required": ["id", "quote", "pdf_page"],
            "properties": {"id": {"type": "string"}, "quote": {"type": "string"}, "pdf_page": {"type": "integer"}}}},
        "evidence": EV,
    }}
RESULT_SCHEMA = {
    "type": "object", "required": ["control_id", "result", "result_quote", "is_exception"],
    "properties": {
        "control_id": {"type": "string"},
        "result": {"enum": ["no_exceptions", "exception", "not_tested", "other"]},
        "result_quote": EV,
        "is_exception": {"type": "boolean"},
        "exception_summary": {"type": "string"},
        "management_response": {"type": "string"},
    }}


def load_criteria():
    for f in ("tsc_2017_aicpa.json", "tsc_2017.json"):
        path = os.path.join(HERE, "soc2v2", "criteria", f)
        if os.path.exists(path):
            d = json.load(open(path))
            return {k: (v.get("text", "") if isinstance(v, dict) else str(v)) for k, v in d["criteria"].items()}, f
    raise SystemExit("No criteria file found in soc2v2/criteria/")


def quote_ok(doc, ev, allowed_pages=None):
    q, p = (ev or {}).get("quote", "") or "", (ev or {}).get("pdf_page")
    if not q.strip():
        return "empty quote"
    if allowed_pages and p not in allowed_pages:
        return f"page {p} was not one of the pages provided ({allowed_pages})"
    r = doc.find_quote(q, p)
    if r["status"] in ("exact", "approximate"):
        return None
    if r["status"].endswith("other_page"):
        return f"quote is on PDF p.{r['found_pages'][0]}, not p.{p}"
    return f"quote not found on p.{p}; copy it character-for-character from the page text"


def id_on_page(doc, cid, page):
    return bool(re.search(r"(?m)^\s*" + re.escape(cid) + r"\b", doc.raw[page - 1])) if page else False


def ask(a, system, prompt, schema, check, label):
    """Ask once; if `check` finds problems, re-ask once with them listed. Returns (answer, problems)."""
    ans = call_ollama(a.host, a.model, system, prompt, schema, a.num_ctx, a.timeout)
    probs = check(ans)
    if probs:
        STATS["retries"] = STATS.get("retries", 0) + 1
        fb = ("Your previous answer had these problems:\n- " + "\n- ".join(probs) +
              "\n\nAnswer again. Use only the pages provided and copy quotes exactly.\n\n")
        ans = call_ollama(a.host, a.model, system, fb + prompt, schema, a.num_ctx, a.timeout)
        probs = check(ans)
        if probs:
            print(f"    [!] {label}: still unresolved after retry: {probs[0]}")
    return ans, probs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--model", default="gemma4:26b")
    ap.add_argument("--criteria", choices=["gaps", "all"], default="gaps")
    ap.add_argument("--pdf")
    ap.add_argument("--password")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--num-ctx", type=int, default=32768, help="keep constant: changing it reloads the model")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--dry-run", action="store_true", help="parser only; no model calls")
    a = ap.parse_args()

    try:
        vdir = resolve_vendor(a.vendor)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    pdf = report_pdf(vdir, a.pdf)
    doc = PdfDoc(pdf, a.password)
    if doc.no_text_pages:
        print(f"[!] {len(doc.no_text_pages)} pages have no text layer (scanned?). OCR first: ocrmypdf in.pdf out.pdf")
    t0 = time.time()

    # ---- D: code parse ----------------------------------------------------------------------------------
    ctrl_pages = _control_pages(doc)
    P = parse_best(doc, ctrl_pages)
    t_parse = time.time() - t0
    controls = {c["id"]: c for c in P["controls"]}
    synthetic = any(c.get("id_source") == "synthetic" for c in P["controls"])
    print(f"[D] parsed {len(controls)} controls, {P['results']['tests']} tests ({P['results']['clean']} clean; "
          f"independent count {P['results'].get('independent_clean_count')}) from PDF p.{ctrl_pages[0]}-{ctrl_pages[-1]} "
          f"in {t_parse:.1f}s [{P['results'].get('layout')}]; {len(P['issues'])} item(s) for the AI")

    gate = P["results"].get("layout_gate", {})
    if gate.get("status") != "PASS":
        print(f"[!] Layout gate: {gate.get('status')}: {gate.get('detail')}. Treat this report's parsed controls as "
              f"unvalidated; review them by hand or use extract_local.py.")
    crit_text, crit_file = load_criteria()
    resolved = {k for c in P["controls"] if c["tests"] for k in c["criteria"]}
    unresolved, exceptions, report, sub = [], [], {}, {}
    crit_answers, agreement = {}, {"same": 0, "ai_subset": 0, "ai_superset": 0, "different": 0}
    prefixes = {re.match(r"[A-Z]+", k).group() for k in P["criteria_pages"]}
    in_scope = [k for k in crit_text if re.match(r"[A-Z]+", k).group() in prefixes]

    if not a.dry_run:
        system = PROMPT + "\n\nPages are marked <<<PDF PAGE n>>>. Use n as pdf_page. Copy quotes exactly as written."
        first_ctrl = ctrl_pages[0] if ctrl_pages else doc.n_pages + 1

        # ---- C1: fixed side-questions ---------------------------------------------------------------------
        front = list(range(1, max(2, min(first_ctrl, 11))))
        print(f"[C] report facts and opinion (PDF p.{front[0]}-{front[-1]})...")
        report = call_ollama(a.host, a.model, system, "Extract ONLY the `report` object from these pages.\n\n"
                             + tagged(doc, front), subschema(["report"], ["report"]), a.num_ctx, a.timeout).get("report", {})
        subp = side_pages(doc, first_ctrl)
        if subp:
            print(f"[C] subservice orgs and CUECs (PDF p.{subp})...")
            sub = call_ollama(a.host, a.model, system, SIDE_ASK + tagged(doc, subp), subschema(["subservice_orgs", "cuecs"], ["subservice_orgs", "cuecs"]),
                              a.num_ctx, a.timeout)

        # ---- C2: results the parser couldn't read as a clean pass -----------------------------------------
        asked = set()
        for iss in P["issues"]:
            c = controls.get(iss["id"])
            if not c or not iss.get("kind") or iss["id"] in asked:   # result questions only, once per control
                continue
            asked.add(iss["id"])
            pages = c["_pages"]
            print(f"[C] control {iss['id']}: {iss['problem']} (PDF p.{pages})")
            prompt = (f"Control {iss['id']} in the testing table on these pages has a result that is not a plain "
                      f"'No exceptions noted.' Read the Results column for control {iss['id']} only. Classify it "
                      f"(exception = a deviation or failure was found; not_tested = the auditor could not or did not "
                      f"test it, e.g. no instances occurred), copy the result wording verbatim as result_quote, and "
                      f"if it is an exception, summarize it and copy any management response.\n\n" + tagged(doc, pages))
            ans, probs = ask(a, system, prompt, RESULT_SCHEMA,
                             lambda r, pg=pages: [p for p in [quote_ok(doc, r.get("result_quote"), pg)] if p],
                             iss["id"])
            if probs:
                unresolved.append({"item": f"result of control {iss['id']}", "problems": probs})
                continue
            q = ans["result_quote"]
            for t in c["tests"]:
                if t["result"] != "no_exceptions":
                    t["result"], t["result_text"] = ans["result"], q["quote"]
            if ans.get("is_exception"):
                exceptions.append({"control_id": iss["id"], "text": ans.get("exception_summary", "") or q["quote"],
                                   "management_response": ans.get("management_response", ""),
                                   "evidence": {"quote": q["quote"], "pdf_page": q["pdf_page"],
                                                "printed_page": doc.printed_page(q["pdf_page"])}})

        # ---- C3: criterion questions ------------------------------------------------------------------------
        cats = report.get("tsc_categories_in_scope") or []
        prefixes = {PREFIX[c] for c in cats if c in PREFIX} or prefixes
        in_scope = [k for k in crit_text if re.match(r"[A-Z]+", k).group() in prefixes]
        ask_list = in_scope if a.criteria == "all" else [k for k in in_scope if k not in resolved]
        print(f"[C] criterion questions: {len(ask_list)} of {len(in_scope)} in-scope criteria "
              f"({'all, as a cross-check' if a.criteria == 'all' else 'only those the parser could not resolve'})")
        for k in ask_list:
            pages = list(P["criteria_pages"].get(k, []))
            if not pages:   # not in the testing tables: look anywhere (e.g. a Section III "not applicable" note)
                pat = re.compile(r"\b" + re.escape(k.lower()) + r"\b")
                pages = [i for i, t in enumerate(doc.norm, 1) if pat.search(t)]
            pages = pages[:4]
            if not pages:
                unresolved.append({"item": f"criterion {k}", "problems": ["criterion ID appears nowhere in the report"]})
                crit_answers[k] = {"status": "not_addressed", "controls": [], "note": "ID not found in PDF text"}
                continue
            idhow = ("This report does not number its controls: use the first five words of the control's "
                     "description as its id. " if synthetic else "")
            prompt = (f"Criterion {k}: {crit_text[k]}\n\nUsing ONLY the pages below: which of the service "
                      f"organization's controls does the report list under criterion {k} in its testing table? For "
                      f"each, give the control ID and a verbatim quote (10-40 words) from that control's row, with "
                      f"pdf_page. {idhow} If the report says {k} is not applicable or is handled by a subservice "
                      f"organization, use status not_applicable and quote that statement as evidence. If the pages "
                      f"don't address {k}, use status not_addressed. Do not use outside knowledge.\n\n" + tagged(doc, pages))

            def check(r, pg=pages, k=k):
                probs = []
                for c in r.get("controls", []):
                    e = quote_ok(doc, c, pg)
                    if e:
                        probs.append(f"control {c.get('id')}: {e}")
                    elif not synthetic and not id_on_page(doc, c.get("id", ""), c.get("pdf_page")) and c.get("id") not in controls:
                        probs.append(f"control ID {c.get('id')} does not appear on p.{c.get('pdf_page')}")
                if r.get("status") == "not_applicable":
                    e = quote_ok(doc, r.get("evidence"))
                    if e:
                        probs.append(f"not_applicable evidence: {e}")
                if r.get("status") == "tested_controls" and not r.get("controls"):
                    probs.append("status is tested_controls but no controls were listed")
                return probs

            print(f"    {k} (PDF p.{pages})")
            ans, probs = ask(a, system, prompt, CRIT_SCHEMA, check, k)
            ai_ok = [c for c in ans.get("controls", []) if not quote_ok(doc, c, pages)]
            if synthetic:   # map each verified quote to the parsed control on that page whose text contains it
                ai_ids = set()
                for c in ai_ok:
                    qn = re.sub(r"\W+", "", c["quote"].lower())[:60]
                    hit = [cid for cid, pc in controls.items() if c["pdf_page"] in pc.get("_pages", [])
                           and qn and qn in re.sub(r"\W+", "", (pc["description"] + " ".join(t["procedure"] for t in pc["tests"])).lower())]
                    ai_ids.add(hit[0] if hit else "?" + c["id"])
            else:
                ai_ids = {c["id"] for c in ai_ok}
            crit_answers[k] = {"status": ans.get("status"), "controls": sorted(ai_ids)}
            if probs:
                unresolved.append({"item": f"criterion {k}", "problems": probs})
            # compare only controls on the pages the model was shown (big reports spread a criterion over many pages)
            code_ids = {cid for cid, c in controls.items() if k in c["criteria"] and c["tests"]
                        and set(c.get("_pages", [])) & set(pages)}
            if k in resolved:          # cross-check against the parser
                if ai_ids == code_ids:
                    agreement["same"] += 1
                elif ai_ids and ai_ids < code_ids:
                    agreement["ai_subset"] += 1
                elif ai_ids > code_ids:
                    agreement["ai_superset"] += 1
                else:
                    agreement["different"] += 1
                crit_answers[k]["parser"] = sorted(code_ids)
            for c in ([] if synthetic else ans.get("controls", [])):   # fill gaps the parser left
                cid = c.get("id")
                # only real IDs: the model sometimes answers "Not explicitly numbered" or "unidentified"
                if cid not in ai_ids or (cid not in controls and not id_on_page(doc, cid, c.get("pdf_page"))):
                    continue
                if cid in controls:
                    if k not in controls[cid]["criteria"]:
                        controls[cid]["criteria"].append(k)
                else:
                    controls[cid] = {"id": cid, "criteria": [k], "description": c["quote"], "tests": [],
                                     "evidence": {"quote": c["quote"], "pdf_page": c["pdf_page"],
                                                  "printed_page": doc.printed_page(c["pdf_page"])}}

    if gate.get("status") != "PASS":
        unresolved.insert(0, {"item": "parser layout gate", "problems": [gate.get("detail", "unvalidated layout")]})
    wall = time.time() - t0
    stats = {"method": f"C on D: code parser + targeted questions ({a.criteria})",
             "wall_seconds": round(wall, 1), "parse_seconds": round(t_parse, 2),
             "controls_parsed": len(P["controls"]), "parser_items_for_ai": len(P["issues"]),
             "parser_layout": P["results"].get("layout"), "parser_clean": P["results"]["clean"],
             "parser_version": P["results"].get("parser_version"), "layout_gate": gate,
             "independent_clean_count": P["results"].get("independent_clean_count"),
             "criteria_in_scope": len(in_scope),
             "criteria_asked": len(crit_answers), "criteria_code_resolved": len(resolved),
             "unresolved": len(unresolved),
             **({"cross_check_vs_parser": agreement} if a.criteria == "all" and not a.dry_run else {}),
             **{k: round(v, 1) if isinstance(v, float) else v for k, v in STATS.items()}}
    out = {
        "schema_version": "1.0",
        "extractor": {"model": "none (parser only)" if a.dry_run else a.model,
                      "tool": f"extract_hybrid.py: soc2v2/parse_controls.py + Ollama ({a.host}); criteria from {crit_file}",
                      "run_at": dt.datetime.now().isoformat(timespec="seconds"),
                      "notes": (f"Hybrid extraction. Controls parsed by code (verbatim by construction); AI answered "
                                f"{STATS['llm_calls']} targeted questions. Unresolved items: "
                                + ("; ".join(f"{u['item']} ({u['problems'][0]})" for u in unresolved) or "none") + "."),
                      "stats": stats,
                      "criterion_answers": crit_answers,
                      "unresolved": unresolved},
        "source": {"file": os.path.basename(pdf), "sha256": doc.sha256, "page_count": doc.n_pages},
        "report": report,
        "subservice_orgs": sub.get("subservice_orgs", []),
        "cuecs": sub.get("cuecs", []),
        "controls": [{k: v for k, v in c.items() if not k.startswith("_")} for c in controls.values()],
        "exceptions": exceptions,
        "claims": [],
    }
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", "parser" if a.dry_run else a.model)
    path = os.path.join(vdir, f"extraction_hybrid_{a.criteria}_{safe}.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"[✓] {len(out['controls'])} controls, {len(out['cuecs'])} CUECs, {len(out['subservice_orgs'])} subservice orgs, "
          f"{len(exceptions)} exceptions; {STATS['llm_calls']} model calls, {STATS['output_tokens']} output tokens, "
          f"{wall:.0f}s -> {os.path.relpath(path, HERE)}")
    if unresolved:
        print(f"[!] {len(unresolved)} unresolved item(s) for the reviewer (listed in extractor.unresolved)")
    print(f"    Next: python3 soc2_review.py verify --vendor {a.vendor} --extraction {os.path.relpath(path, HERE)}")


if __name__ == "__main__":
    main()
