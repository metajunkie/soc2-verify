#!/usr/bin/env python3
"""
Compare extraction methods for one vendor: time, tokens, and how each scored in the verifier.

Reads every extraction*.json in the vendor folder (skipping red-team and v1 imports) and the most recent
verification report for each, then writes Comparison_<vendor>_<date>.md and .json into
Vendors/<vendor>/Review_<date>/v2/. The JSON feeds the presentation's comparison slide.

  python3 compare_methods.py --vendor ExampleCo

Run the verifier on each extraction first (soc2_review.py verify ... --extraction ...).
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from soc2v2.paths import vendor_dir as resolve_vendor  # noqa: E402

SKIP = ("REDTEAM", "v1_import", "_parser")   # red-team copies, v1 imports, parser-only dry runs


def latest_verification(vdir, vendor, stem):
    fs = glob.glob(os.path.join(vdir, "Review_*", "v2", f"Verification_{vendor}_{stem}_*.json"))
    return max(fs, key=os.path.getmtime) if fs else None


def find(results, section, check_prefix):
    for r in results:
        if r["section"] == section and r["check"].startswith(check_prefix):
            return r
    return None


def frac(detail):
    m = re.search(r"(\d+) of (\d+)", detail or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendor", required=True)
    a = ap.parse_args()
    vdir = resolve_vendor(a.vendor)
    rows = []
    bpath = os.path.join(vdir, "extraction_baseline_claude.json")
    base = json.load(open(bpath)) if os.path.exists(bpath) else None
    base_exc = {str(e.get("control_id")) for e in (base or {}).get("exceptions", [])}
    base_rep = (base or {}).get("report", {})

    def vs_baseline(e):
        """How an extraction compares with the reviewer baseline on the facts that drive the decision."""
        if not base or e is base:
            return None
        rep = e.get("report", {}) or {}
        got = {str(x.get("control_id")) for x in e.get("exceptions", []) or []}
        got |= {str(c.get("id")) for c in e.get("controls", []) or []
                for t in c.get("tests", []) or [] if t.get("result") == "exception"}
        facts = {k: (str(rep.get(k) or "").strip().lower() == str(base_rep.get(k) or "").strip().lower())
                 for k in ("period_start", "period_end", "issue_date")}
        facts["opinion"] = (rep.get("opinion") or {}).get("type") == (base_rep.get("opinion") or {}).get("type")
        facts["categories"] = sorted(rep.get("tsc_categories_in_scope") or []) == sorted(base_rep.get("tsc_categories_in_scope") or [])
        facts["auditor"] = bool(rep.get("auditor")) and (base_rep.get("auditor", "").split()[0].lower()
                                                         in str(rep.get("auditor")).lower())
        return {"deviations_found": len(got & base_exc), "deviations_in_baseline": len(base_exc),
                "deviations_missed": sorted(base_exc - got, key=lambda x: (len(x), x)),
                "deviations_extra": sorted(got - base_exc, key=lambda x: (len(x), x)),
                "facts_matching": sum(facts.values()), "facts_checked": len(facts),
                "facts_wrong": [k for k, ok in facts.items() if not ok],
                "cuecs": len(e.get("cuecs", []) or []), "cuecs_in_baseline": len((base or {}).get("cuecs", []) or [])}
    for f in sorted(glob.glob(os.path.join(vdir, "extraction*.json")), key=os.path.getmtime):
        stem = os.path.splitext(os.path.basename(f))[0]
        if any(s in stem for s in SKIP):
            continue
        e = json.load(open(f))
        x = e.get("extractor", {})
        st = x.get("stats", {})
        if not st:   # older runs: recover what we can from the notes
            m = re.search(r"(\d+)s total", x.get("notes", ""))
            st = {"method": "A: full transcription" if "local" in stem else "cloud model (manual)",
                  "wall_seconds": float(m.group(1)) if m else None}
            body = len(json.dumps(e.get("controls", [])) + json.dumps(e.get("report", {})) + json.dumps(e.get("cuecs", [])))
            st["output_tokens_est"] = round(body / 3.8)     # rough: JSON averages ~3.8 characters per token
        vf = latest_verification(vdir, a.vendor, stem)
        v = json.load(open(vf)) if vf else {"results": []}
        res = v.get("results", [])
        comp = find(res, "Completeness", "Every control")
        quotes = find(res, "Evidence quotes", "Quotes verified")
        counts = {s: sum(1 for r in res if r["status"] == s) for s in ("FAIL", "REVIEW", "PASS")}
        tr = find(res, "Completeness", "Test results")
        m = re.search(r"\((\d+) clean.*?PDF: (\d+) clean", tr["detail"]) if tr else None
        if tr and not m:   # older verifier wording: "N tests (E with exceptions); PDF: P clean"
            m2 = re.search(r"(\d+) tests \((\d+) with exceptions\); PDF: (\d+) clean", tr["detail"])
            m = (int(m2.group(1)) - int(m2.group(2)), int(m2.group(3))) if m2 else None
        elif m:
            m = (int(m.group(1)), int(m.group(2)))
        rows.append({
            "extraction": os.path.basename(f), "model": x.get("model"), "method": st.get("method"),
            "wall_seconds": st.get("wall_seconds"), "llm_calls": st.get("llm_calls"),
            "prompt_tokens": st.get("prompt_tokens"),
            "output_tokens": st.get("output_tokens") if st.get("output_tokens") is not None else st.get("output_tokens_est"),
            "output_tokens_estimated": "output_tokens" not in st,
            "controls": frac(comp["detail"]) if comp else None,
            "quotes": frac(quotes["detail"]) if quotes else None,
            "clean_results": list(m) if m else None,
            "verifier": counts if vf else None, "overall_fail": counts["FAIL"] > 0 if vf else None,
            "unresolved": st.get("unresolved"), "cross_check": st.get("cross_check_vs_parser"),
            "retries": st.get("retries"), "verification": os.path.basename(vf) if vf else None,
            "vs_baseline": vs_baseline(e) if base and os.path.abspath(f) != os.path.abspath(bpath) else None,
        })

    today = dt.date.today().isoformat()
    outdir = os.path.join(vdir, f"Review_{today}", "v2")
    os.makedirs(outdir, exist_ok=True)
    json.dump({"vendor": a.vendor, "generated": today, "rows": rows},
              open(os.path.join(outdir, f"Comparison_{a.vendor}_{today}.json"), "w"), indent=2)

    def fmt_n(n, est=False):
        return "-" if n is None else (f"~{n:,}" if est else f"{n:,}")

    lines = [f"# Extraction method comparison: {a.vendor} ({today})", "",
             "| Method | Model | Time | Model calls | Output tokens | Controls found | Quotes verified | Clean results (PDF) | Verifier FAIL / REVIEW |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        t = "-" if r["wall_seconds"] is None else (f"{r['wall_seconds'] / 60:.1f} min" if r["wall_seconds"] >= 60
                                                   else f"{r['wall_seconds']:.0f} s")
        c = f"{r['controls'][0]} of {r['controls'][1]}" if r["controls"] else "-"
        q = f"{r['quotes'][0]} of {r['quotes'][1]}" if r["quotes"] else "-"
        vv = f"{r['verifier']['FAIL']} / {r['verifier']['REVIEW']}" if r["verifier"] else "not verified yet"
        cr = f"{r['clean_results'][0]} ({r['clean_results'][1]})" if r["clean_results"] else "-"
        lines.append(f"| {r['method']} | {r['model']} | {t} | {fmt_n(r['llm_calls'])} | "
                     f"{fmt_n(r['output_tokens'], r['output_tokens_estimated'])} | {c} | {q} | {cr} | {vv} |")
    lines += ["", "~ = estimated from the size of the JSON (run predates token counting).", ""]
    if base:
        lines += ["## Against the reviewer baseline", "",
                  "| Method | Controls with deviations found | Missed | Extra | Report facts right | Wrong | CUECs/UERs |",
                  "|---|---|---|---|---|---|---|"]
        for r in rows:
            b = r.get("vs_baseline")
            if not b:
                continue
            lines.append(f"| {r['method']} | {b['deviations_found']} of {b['deviations_in_baseline']} | "
                         f"{', '.join(b['deviations_missed']) or '-'} | {', '.join(b['deviations_extra']) or '-'} | "
                         f"{b['facts_matching']} of {b['facts_checked']} | {', '.join(b['facts_wrong']) or '-'} | "
                         f"{b['cuecs']} (baseline {b['cuecs_in_baseline']}) |")
        lines.append("")
    for r in rows:
        if r["cross_check"]:
            cc = r["cross_check"]
            lines.append(f"- **{r['extraction']}** cross-check, model vs parser per criterion: {cc['same']} identical, "
                         f"{cc['ai_subset']} model listed fewer, {cc['ai_superset']} model listed more, {cc['different']} different.")
        if r["unresolved"]:
            lines.append(f"- **{r['extraction']}**: {r['unresolved']} item(s) unresolved after one retry (see extractor.unresolved).")
    path = os.path.join(outdir, f"Comparison_{a.vendor}_{today}.md")
    open(path, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n[✓] {os.path.relpath(path, HERE)} (+ .json for the deck)")


if __name__ == "__main__":
    main()
