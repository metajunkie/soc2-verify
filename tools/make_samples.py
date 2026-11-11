#!/usr/bin/env python3
"""
Generate three synthetic SOC 2 Type II reports, one per table layout the parser supports, plus the
ground-truth extraction for each. Everything here is fictional: companies, auditors, controls, results.

  python3 tools/make_samples.py          # writes Vendors/Sample-A|B|C/  (needs: pip install reportlab)

Each sample contains a known deviation, a control that was not tested, user-entity responsibilities, and
(A and C) a carved-out subservice organization. Because the generator knows the right answers, the
ground-truth extraction (extraction_truth.json) can score any extraction method exactly.

Criterion headings use short topic labels from soc2v2/criteria/tsc_2017.json, not AICPA text.
"""
import datetime as dt
import json
import os
import re
import sys

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from soc2v2.pdftext import PdfDoc, normalize  # noqa: E402

rl_config.invariant = 1          # byte-identical PDFs on every run (no timestamps or random IDs)
LABELS = json.load(open(os.path.join(HERE, "soc2v2", "criteria", "tsc_2017.json")))["criteria"]
BODY = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=13, spaceAfter=7)
H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=19, spaceAfter=10)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11.5, leading=15, spaceBefore=6, spaceAfter=6)
CELL = ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, leading=10.5)
CELLB = ParagraphStyle("cb", parent=CELL, fontName="Helvetica-Bold")

SAMPLES = {
    "A": dict(folder="Sample-A", company="Acme Analytics, Inc.", system="Acme Insights Platform",
              auditor="Cedar Ridge Assurance LLP", city="Columbus, Ohio",
              start=dt.date(2025, 7, 1), end=dt.date(2026, 6, 30), issued=dt.date(2026, 8, 14), datefmt="us",
              layout="headings_numbered", result_ok="No exceptions noted.", exc_word="Exception noted.",
              subservice="Example Cloud Hosting, Inc.", printed_offset=1,
              services=["Acme Insights Platform", "Acme browser extension | Acme chrome extension"]),
    "B": dict(folder="Sample-B", company="Larkspur Data Services, LLC", system="Larkspur Cloud Console",
              auditor="Example Assurance LLP", city="Austin, Texas",
              start=dt.date(2025, 4, 1), end=dt.date(2026, 3, 31), issued=dt.date(2026, 5, 12), datefmt="eu",
              layout="numbered_criteria_column", result_ok="No deviations noted.", exc_word="Deviation noted.",
              subservice=None, printed_offset=0, bridge_through=dt.date(2026, 8, 31),
              services=["Larkspur Cloud Console"]),
    "C": dict(folder="Sample-C", company="Tidewater Edge Systems, Inc.", system="Tidewater Edge Gateway",
              auditor="Sample Attest Partners, LLC", city="Denver, Colorado",
              start=dt.date(2024, 11, 1), end=dt.date(2025, 10, 31), issued=dt.date(2026, 2, 2), datefmt="us",
              layout="headings_unnumbered", result_ok="No exceptions noted.", exc_word="Exception noted.",
              subservice="Example Cloud Hosting, Inc.", printed_offset=0,
              services=["Tidewater Edge Gateway"]),
}
CRITERIA = [k for k in LABELS if k.startswith("CC")] + ["A1.1", "A1.2", "A1.3"]
DEVIATION_ON, UNTESTED_ON = "CC6.2", "CC7.5"


def fmt(d, style):
    return d.strftime("%B ") + str(d.day) + d.strftime(", %Y") if style == "us" else d.strftime("%d %B %Y")


def controls_for(s):
    """Deterministic, generic controls: one or two per criterion, with known results."""
    out, seq = [], 0
    for k in CRITERIA:
        label = LABELS[k].lower()
        n_ctrl = 2 if k in ("CC6.1", "CC6.2", "CC7.2", "CC8.1") else 1
        for j in range(1, n_ctrl + 1):
            seq += 1
            desc = (f"Management maintains documented procedures for {label}. The procedures are reviewed "
                    f"and approved at least annually.") if j == 1 else \
                   (f"Changes affecting {label} are tracked, reviewed and approved before they take effect.")
            tests = [(f"Inspected the documented procedures and evidence of the annual review to determine that "
                      f"procedures for {label} were in place and approved.", "ok")]
            if j == 1 and k in ("CC6.1", "CC8.1", "A1.2"):
                tests.append(("For a sample of changes during the period, inspected the change record to determine "
                              "that each change was reviewed and approved.", "ok"))
            if k == DEVIATION_ON and j == 2:
                desc = "Access for terminated personnel is removed within one business day of termination."
                tests = [("For a sample of twenty-five terminated users, inspected access removal records to "
                          "determine that access was removed within one business day.", "exception")]
            if k == UNTESTED_ON:
                desc = "Recovery from security incidents follows the documented incident recovery procedure."
                tests = [("Inquired of management and inspected the incident log to determine whether any "
                          "incidents requiring recovery occurred during the period.", "not_tested")]
            cid = {"headings_numbered": f"{'' if k.startswith('CC') else 'A'}{k.replace('CC', '').replace('A', '')}.{j}",
                   "numbered_criteria_column": str(seq),
                   "headings_unnumbered": f"{k}#{j}"}[s["layout"]]
            out.append({"id": cid, "criterion": k, "description": desc, "tests": tests})
    return out


EXC_DETAIL = ("For one (1) of twenty-five (25) sampled terminated users, access was removed nine business days "
              "after termination.")
MGMT = ("Management's response: the account was disabled when the exception was identified, and no activity "
        "occurred after the termination date. An automated deprovisioning check was added in May.")
NT_TEXT = "Not tested. No incidents requiring recovery occurred during the period."


def result_text(s, kind):
    return {"ok": s["result_ok"], "exception": s["exc_word"] + " " + EXC_DETAIL + " " + MGMT,
            "not_tested": NT_TEXT}[kind]


def front_matter(s, story):
    us = s["datefmt"]
    period = f"{fmt(s['start'], us)} to {fmt(s['end'], us)}"
    cats = "security and availability"
    story += [Spacer(1, 1.6 * inch), Paragraph(s["company"], H1), Paragraph("SOC 2 Type II Report", H1),
              Paragraph(f"{s['system']}", H2), Paragraph(f"For the period {period}", BODY),
              Paragraph(f"Independent service auditor: {s['auditor']}", BODY),
              Paragraph("This is a fictional report generated for software testing. It describes no real "
                        "organization, system or examination.", BODY), PageBreak()]
    story += [Paragraph("Section I: Independent Service Auditor's Report", H1),
              Paragraph(f"To the management of {s['company']}", BODY),
              Paragraph("Scope", H2),
              Paragraph(f"We have examined {s['company']}'s description of its {s['system']} for the period {period} "
                        f"(description) and the suitability of the design and operating effectiveness of controls stated "
                        f"in the description, based on the trust services criteria relevant to {cats} (applicable "
                        f"trust services criteria).", BODY)]
    if s["subservice"]:
        story.append(Paragraph(
            f"{s['company']} uses {s['subservice']}, a subservice organization, for infrastructure hosting. The "
            f"description includes only the controls of {s['company']} and excludes the controls of "
            f"{s['subservice']}; that is, the subservice organization is presented using the carve-out method.", BODY))
    story += [Paragraph("Service auditor's responsibilities", H2),
              Paragraph("Our responsibility is to express an opinion on the description and on the suitability of "
                        "the design and operating effectiveness of the controls, based on our examination.", BODY),
              Paragraph("Opinion", H2),
              Paragraph(f"In our opinion, in all material respects, the description presents the {s['system']} that "
                        f"was designed and implemented throughout the period {period}, and the controls stated in the "
                        f"description were suitably designed and operated effectively to provide reasonable assurance "
                        f"that the service commitments and system requirements were achieved based on the applicable "
                        f"trust services criteria.", BODY),
              Spacer(1, 12), Paragraph(s["auditor"], BODY), Paragraph(s["city"], BODY),
              Paragraph(fmt(s["issued"], us), BODY), PageBreak()]
    story += [Paragraph("Section II: Management's Assertion", H1),
              Paragraph(f"We have prepared the description of the {s['system']} for the period {period}. We confirm "
                        f"that the description presents the system fairly and that the controls were suitably designed "
                        f"and operated effectively throughout the period.", BODY), PageBreak()]
    story += [Paragraph("Section III: Description of the System", H1),
              Paragraph("Services provided", H2),
              Paragraph(f"{s['company']} provides the {s['system']}, a hosted service used by business customers.", BODY)]
    if s["subservice"]:
        story += [Paragraph("Subservice organizations", H2),
                  Paragraph(f"{s['company']} uses {s['subservice']} for cloud hosting services. The following controls "
                            f"are expected to be implemented by the subservice organization: restricting physical access "
                            f"to data centers; maintaining environmental protections; and backing up hosted data.", BODY)]
    story.append(Paragraph("Complementary user entity controls", H2))
    for u in uecs(s):
        story.append(Paragraph(u, BODY))
    story.append(PageBreak())


def uecs(s):
    if s["layout"] == "numbered_criteria_column":
        return ["Customers are responsible for managing access for their own users of the console.",
                "Customers are responsible for reviewing the activity logs available to them.",
                "Customers are responsible for notifying the service organization of suspected security incidents.",
                "Customers are responsible for the accuracy of the data they submit."]
    return ["User entities are responsible for granting access only to authorized and trained personnel.",
            "User entities are responsible for removing access for their personnel who no longer need it.",
            "User entities are responsible for reporting suspected security incidents promptly.",
            "User entities are responsible for protecting the credentials used to access the system."]


def section_iv(s, ctrls, story):
    story += [Paragraph("Section IV: Trust Services Criteria, Related Controls and Tests of Controls", H1),
              Paragraph("The tables below list each control, the tests performed by the service auditor and the "
                        "results of those tests.", BODY)]
    P = lambda t, st=CELL: Paragraph(t, st)  # noqa: E731
    lay = s["layout"]
    if lay == "headings_numbered":
        rows, style = [[P("Control", CELLB), P("Key Control Activity", CELLB), P("Tests Performed", CELLB), P("Results", CELLB)]], []
        last = None
        for c in ctrls:
            if c["criterion"] != last:
                rows.append([P(f"{c['criterion']} - {LABELS[c['criterion']]}", CELLB), "", "", ""])
                style.append(("SPAN", (0, len(rows) - 1), (-1, len(rows) - 1)))
                style.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.HexColor("#E8EEF5")))
                last = c["criterion"]
            for i, (proc, kind) in enumerate(c["tests"]):
                rows.append([P(c["id"]) if i == 0 else "", P(c["description"]) if i == 0 else "", P(proc),
                             P(result_text(s, kind))])
        widths = [0.6, 2.2, 3.1, 1.4]
    elif lay == "numbered_criteria_column":
        rows, style = [[P("Control Description", CELLB), P("SOC 2 Criteria Reference", CELLB), P("Tests Performed", CELLB), P("Results", CELLB)]], []
        for c in ctrls:
            crit = c["criterion"] + (", CC2.1" if c["criterion"] == "CC6.1" else "")
            for i, (proc, kind) in enumerate(c["tests"]):
                rows.append([P(f"{c['id']}. {c['description']}") if i == 0 else "", P(crit) if i == 0 else "", P(proc),
                             P(result_text(s, kind))])
        widths = [2.3, 1.0, 2.7, 1.3]
    else:
        rows, style = [[P("TSC Reference", CELLB), P("Trust Services Criteria and Applicable Control Activities", CELLB),
                        P("Service Auditor's Tests", CELLB), P("Results of Tests", CELLB)]], []
        last = None
        for c in ctrls:
            if c["criterion"] != last:
                rows.append([P(c["criterion"], CELLB), P(LABELS[c["criterion"]], CELLB), "", ""])
                style.append(("SPAN", (1, len(rows) - 1), (-1, len(rows) - 1)))
                style.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.HexColor("#E8EEF5")))
                last = c["criterion"]
            for i, (proc, kind) in enumerate(c["tests"]):
                rows.append(["", P(c["description"]) if i == 0 else "", P(proc), P(result_text(s, kind))])
        widths = [0.7, 2.4, 2.9, 1.3]
    t = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D0D8E4")),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)] + style))
    story.append(t)


def build_pdf(s, path, ctrls):
    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(0.6 * inch, 10.6 * inch, f"{s['company']}  |  SOC 2 Type II  |  Confidential (fictional sample)")
        n = doc.page - s["printed_offset"]
        if n >= 1 and doc.page > 1:
            canvas.drawRightString(7.9 * inch, 0.45 * inch, f"Page {n}")
        canvas.restoreState()
    story = []
    front_matter(s, story)
    section_iv(s, ctrls, story)
    SimpleDocTemplate(path, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.7 * inch,
                      bottomMargin=0.7 * inch, title=f"{s['company']} SOC 2 Type II (fictional sample)").build(
        story, onFirstPage=page, onLaterPages=page)


def build_bridge(s, path):
    us = s["datefmt"]
    story = [Paragraph(f"{s['company']}: Bridge Letter", H1),
             Paragraph(f"{s['company']}'s most recent SOC 2 Type II report covers the period {fmt(s['start'], us)} to "
                       f"{fmt(s['end'], us)}. Management confirms that there have been no material changes to the "
                       f"system or its controls between {fmt(s['end'], us)} and {fmt(s['bridge_through'], us)}.", BODY),
             Paragraph("This is a fictional bridge letter generated for software testing.", BODY)]
    SimpleDocTemplate(path, pagesize=letter).build(story)


def locate(doc, text, prefer=None, min_words=5, max_words=24):
    """Longest opening run of `text` found verbatim on some page (preferring `prefer`)."""
    words = text.split()
    pages = ([prefer] if prefer else []) + [p for p in range(1, doc.n_pages + 1) if p != prefer]
    for n in range(min(max_words, len(words)), min_words - 1, -1):
        q = normalize(" ".join(words[:n]))
        for p in pages:
            hay = doc.norm[p - 1]
            if q in hay or q.replace(" ", "") in hay.replace(" ", ""):
                return " ".join(words[:n]), p
    raise SystemExit(f"could not place quote: {text[:60]}")


def truth(s, path, ctrls):
    doc = PdfDoc(path)
    us = s["datefmt"]

    def ev(t, prefer=None, **kw):
        q, p = locate(doc, t, prefer, **kw)
        return {"quote": q, "pdf_page": p, "printed_page": doc.printed_page(p)}
    controls, exceptions = [], []
    page_hint = None
    for c in ctrls:
        e = ev((f"{c['id']}. " if s["layout"] == "numbered_criteria_column" else "") + c["description"], page_hint)
        page_hint = e["pdf_page"]
        crit = [c["criterion"]] + (["CC2.1"] if s["layout"] == "numbered_criteria_column" and c["criterion"] == "CC6.1" else [])
        tests = [{"procedure": proc, "result": {"ok": "no_exceptions"}.get(kind, kind),
                  "result_text": result_text(s, kind)} for proc, kind in c["tests"]]
        rec = {"id": c["id"], "criteria": crit, "description": c["description"], "tests": tests, "evidence": e}
        if s["layout"] == "headings_unnumbered":
            rec["id_source"] = "synthetic"
        controls.append(rec)
        if any(k == "exception" for _, k in c["tests"]):
            exceptions.append({"control_id": c["id"], "text": EXC_DETAIL, "management_response": MGMT,
                               "evidence": ev(EXC_DETAIL, page_hint)})
    period_start, period_end = fmt(s["start"], us), fmt(s["end"], us)
    report = {"service_org": s["company"], "system_name": s["system"], "products_in_scope": [s["system"]],
              "report_type": "SOC 2 Type II", "period_start": s["start"].isoformat(), "period_end": s["end"].isoformat(),
              "issue_date": s["issued"].isoformat(), "auditor": s["auditor"],
              "opinion": {"type": "unqualified", "evidence": ev(f"In our opinion, in all material respects, the description presents the {s['system']}")},
              "tsc_categories_in_scope": ["Security", "Availability"],
              "evidence": [ev(f"For the period {period_start} to {period_end}"), ev(f"{s['city']} {fmt(s['issued'], us)}")]}
    sub = []
    if s["subservice"]:
        sub = [{"name": s["subservice"], "method": "carve-out", "services": "Cloud hosting",
                "evidence": ev(f"{s['company']} uses {s['subservice']} for cloud hosting services.")}]
    cuecs = [{"text": u, "criteria": [], "evidence": ev(u)} for u in uecs(s)]
    return {"schema_version": "1.0",
            "extractor": {"model": "ground truth (generator)", "tool": "tools/make_samples.py",
                          "run_at": "2026-11-11T00:00:00", "notes": "Fictional sample; every value is known by construction.",
                          "stats": {"method": "Ground truth"}},
            "source": {"file": os.path.basename(path), "sha256": doc.sha256, "page_count": doc.n_pages},
            "report": report, "subservice_orgs": sub, "cuecs": cuecs, "controls": controls,
            "exceptions": exceptions, "claims": []}


def main():
    for key, s in SAMPLES.items():
        vdir = os.path.join(HERE, "Vendors", s["folder"])
        os.makedirs(vdir, exist_ok=True)
        ctrls = controls_for(s)
        pdf = os.path.join(vdir, f"{s['company'].split(',')[0]} SOC 2 Type II Report (fictional).pdf")
        build_pdf(s, pdf, ctrls)
        if s.get("bridge_through"):
            build_bridge(s, os.path.join(vdir, f"{s['company'].split(',')[0]} Bridge Letter (fictional).pdf"))
        t = truth(s, pdf, ctrls)
        json.dump(t, open(os.path.join(vdir, "extraction_truth.json"), "w"), indent=2)
        prof = ["# Fictional sample scope profile", f'client: "Sample Client"', f'vendor: "{s["company"]}"',
                'reviewer: "Sample Reviewer"', "services_used:"] + [f'  - "{x}"' for x in s["services"]] + [
                "data_types:", "  - business_contact", "required_tsc_categories:", "  - Security", "  - Availability",
                "criticality: medium", "max_report_age_months: 12", "bridge_letter_after_months: 3", "overlays: []"]
        open(os.path.join(vdir, "scope_profile.yaml"), "w").write("\n".join(prof) + "\n")
        print(f"[✓] {s['folder']}: {PdfDoc(pdf).n_pages} pages, {len(ctrls)} controls, "
              f"{sum(len(c['tests']) for c in ctrls)} tests ({s['layout']})")


if __name__ == "__main__":
    main()
