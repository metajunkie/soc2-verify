"""Generate the CyberCede Vendor Use Profile workbook (blank template + filled-in example)."""
import sys
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

F = "Arial"
NAVY = "172D5F"
YELLOW = PatternFill("solid", fgColor="FFF2B3")
GREY = PatternFill("solid", fgColor="E7EBF0")
HEAD = PatternFill("solid", fgColor=NAVY)
thin = Side(style="thin", color="C8CED6")
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)

YN = ["Yes", "No"]
LISTS = {
    "yn": YN,
    "ynu": ["Yes", "No", "Unknown"],
    "volume": ["None", "Under 1K", "1K-10K", "10K-100K", "Over 100K"],
    "flow": ["We send data to the vendor", "Vendor sends data to us", "Both directions", "No data exchanged"],
    "impact": ["Low", "Medium", "High", "Critical"],
    "rto": ["Under 4 hours", "Under 24 hours", "1-5 days", "Over 5 days"],
}

# (id, question, list or None for free text, guidance, example answer)
QUESTIONS = [
    ("A", "Identification", None, None, None),
    ("A1", "Client", None, "Who this review is for.", "Internal review (example)"),
    ("A2", "Vendor legal name", None, "As it appears on the contract / SOC report.", "Example Data Co."),
    ("A3", "Products / services used (one per line; alternatives separated by |)", None,
     "Each line is checked against the SOC report's system description. Use the vendor's product names. "
     "Alternatives on one line (a | b) pass if any one appears.", "Example Directory\nbrowser extension | chrome extension"),
    ("A4", "What does the vendor do for you?", None, "One or two plain sentences.",
     "Contact directory used by sales and marketing; data pulled into the CRM."),
    ("A5", "Business owner (client side)", None, "Accountable for the relationship and the CUECs.", "VP Sales Operations"),
    ("A6", "Reviewer", None, None, "Your name, credentials"),
    ("A7", "Date completed", None, "YYYY-MM-DD", "2026-11-10"),
    ("B", "Data and integration", None, None, None),
    ("B1", "Business contact info (names, work email/phone)", "yn", None, "Yes"),
    ("B2", "Personal data about individuals (PII)", "yn", "Any data about an identifiable person.", "Yes"),
    ("B3", "Sensitive personal data (SSN, government ID, biometrics, special categories)", "yn", None, "No"),
    ("B4", "Health information (PHI)", "yn", None, "No"),
    ("B5", "Payment card data", "yn", None, "No"),
    ("B6", "Financial accounts or financial records", "yn", None, "No"),
    ("B7", "Credentials, secrets or encryption keys", "yn", "Includes API tokens the vendor holds for your systems.", "No"),
    ("B8", "Source code", "yn", None, "No"),
    ("B9", "Other confidential business information", "yn", "Contracts, pricing, strategy, customer lists.", "No"),
    ("B10", "Number of personal-data records the vendor holds for you", "volume", None, "Under 1K"),
    ("B11", "Data flow direction", "flow", "Integrations that push your CRM data to the vendor count as 'we send'.", "Vendor sends data to us"),
    ("B12", "Where is your data stored / processed (regions)?", None, None, "United States"),
    ("B13", "Does the vendor use your data for AI/ML training or AI features?", "ynu", "Check the contract and DPA.", "Unknown"),
    ("B14", "Integrations and access paths", None, "SSO, APIs, browser extensions, agents, file transfers.",
     "Web app; browser extension; CRM integration"),
    ("C", "Criticality and obligations", None, None, None),
    ("C1", "Impact if unavailable for one business day", "impact", None, "Medium"),
    ("C2", "Maximum tolerable downtime (RTO)", "rto", None, "1-5 days"),
    ("C3", "Supports financial reporting (ICOFR / SOX)?", "yn", "If Yes, a bridge letter is mandatory for any gap.", "No"),
    ("C4", "Processes or calculates data you rely on for accuracy (billing, payroll, transactions)?", "yn", None, "No"),
    ("C5", "Privacy-law obligations for this data (GDPR, CCPA/CPRA, HIPAA, other)?", "yn", None, "No"),
    ("C6", "Vendor has privileged or network access into your environment?", "yn", None, "No"),
    ("C7", "Your customers or the public interact with it?", "yn", None, "No"),
]

TIERS = ["Tier 1 - Critical", "Tier 2 - High", "Tier 3 - Moderate", "Tier 4 - Low"]
RULES = [  # tier, bridge-letter threshold (months), max report age (months), evidence, cadence
    ("Tier 1 - Critical", 3, 12, "SOC 2 Type II (current); bridge letter for gaps; pen test summary; CUEC owners assigned", "Annually, plus event-driven"),
    ("Tier 2 - High", 3, 12, "SOC 2 Type II, or ISO 27001 certificate + Statement of Applicability", "Annually"),
    ("Tier 3 - Moderate", 6, 12, "SOC 2 Type II, ISO 27001, or security questionnaire (e.g. CSA CAIQ-Lite)", "Every 2 years"),
    ("Tier 4 - Low", 12, 24, "Security questionnaire or attestation", "At onboarding and renewal"),
]


def build(path, example):
    wb = Workbook()
    ws = wb.active
    ws.title = "Vendor Use Profile"
    lists = wb.create_sheet("Lists")
    rules = wb.create_sheet("Rules")

    # ---------------- Lists (dropdown sources)
    col = 1
    ranges = {}
    for name, vals in LISTS.items():
        lists.cell(1, col, name).font = Font(name=F, bold=True)
        for i, v in enumerate(vals, 2):
            lists.cell(i, col, v).font = Font(name=F)
        L = lists.cell(1, col).column_letter
        ranges[name] = f"Lists!${L}$2:${L}${len(vals) + 1}"
        col += 1
    lists.sheet_state = "hidden"

    # ---------------- Rules (CyberCede defaults - tune here)
    rules["A1"] = "CyberCede tiering defaults: edit the values in this table to change policy. Formulas and the YAML generator both read it."
    rules["A1"].font = Font(name=F, bold=True)
    hdr = ["Tier", "Bridge letter if gap exceeds (months)", "Max report age (months)", "Evidence required", "Review cadence"]
    for j, h in enumerate(hdr, 1):
        c = rules.cell(3, j, h)
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = HEAD
    for i, row in enumerate(RULES, 4):
        for j, v in enumerate(row, 1):
            c = rules.cell(i, j, v)
            c.font = Font(name=F, color="0000FF" if j in (2, 3) else "000000")
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = BOX
    rules["A9"] = "Tier logic (first match wins):"
    rules["A9"].font = Font(name=F, bold=True)
    logic = ["Tier 1: PHI, payment card, credentials/keys, sensitive personal data, privileged access, Critical impact, ICOFR, or > 100K personal records.",
             "Tier 2: PII, financial records, source code, confidential business info, High impact, accuracy-critical processing, customer-facing, or 10K-100K records.",
             "Tier 3: business contact info only, Medium impact, or any personal records.",
             "Tier 4: everything else.",
             "Required TSC categories: Security always; Confidentiality if any non-public data (B2-B9); Availability if High/Critical impact or RTO under 24 hours; "
             "Processing Integrity if ICOFR or accuracy-critical; Privacy if health or sensitive personal data AND privacy-law obligations.",
             "Recommended (not required): Availability at Medium impact; Privacy whenever personal data is involved.",
             "ICOFR = Yes makes a bridge letter mandatory for any gap.",
             "These are CyberCede starting defaults (professional judgment), not an external standard."]
    for i, t in enumerate(logic, 10):
        rules.cell(i, 1, t).font = Font(name=F)
    for colw, w in zip("ABCDE", (22, 22, 18, 60, 26)):
        rules.column_dimensions[colw].width = w

    # ---------------- Main sheet
    ws["A1"] = "CyberCede Vendor Use Profile"
    ws["A1"].font = Font(name=F, bold=True, size=16, color=NAVY)
    ws["A2"] = ("How the client uses this vendor. Fill in the yellow cells; grey cells calculate. "
                "Then run: python3 profile_from_xlsx.py <this file>  to produce scope_profile.yaml for the SOC 2 verifier.")
    ws["A2"].font = Font(name=F, italic=True, size=10)
    ws.merge_cells("A2:D2")
    ws["A2"].alignment = Alignment(wrap_text=True)
    ws.row_dimensions[2].height = 30
    for j, h in enumerate(["ID", "Question", "Answer", "Guidance"], 1):
        c = ws.cell(4, j, h)
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = HEAD
    r = 5
    cell = {}
    for qid, q, lst, guide, ex in QUESTIONS:
        if len(qid) == 1:
            ws.cell(r, 1, q).font = Font(name=F, bold=True, size=12, color=NAVY)
            r += 1
            continue
        ws.cell(r, 1, qid).font = Font(name=F)
        ws.cell(r, 2, q).font = Font(name=F)
        a = ws.cell(r, 3, ex if example else None)
        a.font = Font(name=F, color="0000FF")
        a.fill = YELLOW
        if guide:
            ws.cell(r, 4, guide).font = Font(name=F, size=9, color="555555")
        for j in range(1, 5):
            ws.cell(r, j).border = BOX
            ws.cell(r, j).alignment = Alignment(wrap_text=True, vertical="top")
        if lst:
            dv = DataValidation(type="list", formula1=f"={ranges[lst]}", allow_blank=True)
            dv.error, dv.errorTitle = "Pick a value from the list", "Invalid entry"
            ws.add_data_validation(dv)
            dv.add(a)
        cell[qid] = f"$C${r}"
        r += 1

    # ---------------- Calculated section
    r += 1
    ws.cell(r, 1, "Results (calculated)").font = Font(name=F, bold=True, size=12, color=NAVY)
    r += 1
    c = cell
    nonpublic = f'OR({c["B2"]}="Yes",{c["B3"]}="Yes",{c["B4"]}="Yes",{c["B5"]}="Yes",{c["B6"]}="Yes",{c["B7"]}="Yes",{c["B8"]}="Yes",{c["B9"]}="Yes")'
    t1 = (f'OR({c["B3"]}="Yes",{c["B4"]}="Yes",{c["B5"]}="Yes",{c["B7"]}="Yes",{c["C6"]}="Yes",'
          f'{c["C1"]}="Critical",{c["C3"]}="Yes",{c["B10"]}="Over 100K")')
    t2 = (f'OR({c["B2"]}="Yes",{c["B6"]}="Yes",{c["B8"]}="Yes",{c["B9"]}="Yes",{c["C1"]}="High",'
          f'{c["C4"]}="Yes",{c["C7"]}="Yes",{c["B10"]}="10K-100K")')
    t3 = f'OR({c["B1"]}="Yes",{c["C1"]}="Medium",AND({c["B10"]}<>"None",{c["B10"]}<>""))'
    answered = f'COUNTA({c["B1"]}:{c["B10"]},{c["C1"]}:{c["C7"]})'
    tier_f = f'=IF({answered}=0,"(answer sections B and C)",IF({t1},"{TIERS[0]}",IF({t2},"{TIERS[1]}",IF({t3},"{TIERS[2]}","{TIERS[3]}"))))'
    tier_row = r
    T = f"$C${tier_row}"
    avail_req = f'OR({c["C1"]}="High",{c["C1"]}="Critical",{c["C2"]}="Under 4 hours",{c["C2"]}="Under 24 hours")'
    pi_req = f'OR({c["C3"]}="Yes",{c["C4"]}="Yes")'
    priv_req = f'AND(OR({c["B3"]}="Yes",{c["B4"]}="Yes"),{c["C5"]}="Yes")'
    personal = f'OR({c["B2"]}="Yes",{c["B3"]}="Yes",{c["B4"]}="Yes",AND({c["B10"]}<>"None",{c["B10"]}<>""))'
    results = [
        ("D1", "Vendor tier", tier_f),
        ("D2", "Required TSC categories",
         f'="Security"&IF({nonpublic},", Confidentiality","")&IF({avail_req},", Availability","")'
         f'&IF({pi_req},", Processing Integrity","")&IF({priv_req},", Privacy","")'),
        ("D3", "Recommended TSC categories",
         f'=IF(AND({c["C1"]}="Medium",NOT({avail_req})),"Availability","")'
         f'&IF(AND({personal},NOT({priv_req})),IF(AND({c["C1"]}="Medium",NOT({avail_req})),", ","")&"Privacy","")'),
        ("D4", "Bridge letter rule",
         f'=IFERROR(IF({c["C3"]}="Yes","Mandatory for any gap (ICOFR)","Request if gap exceeds "&INDEX(Rules!$B$4:$B$7,MATCH({T},Rules!$A$4:$A$7,0))&" months"),"")'),
        ("D5", "Max report age (months)", f"=IFERROR(INDEX(Rules!$C$4:$C$7,MATCH({T},Rules!$A$4:$A$7,0)),\"\")"),
        ("D6", "Evidence required", f"=IFERROR(INDEX(Rules!$D$4:$D$7,MATCH({T},Rules!$A$4:$A$7,0)),\"\")"),
        ("D7", "Review cadence", f"=IFERROR(INDEX(Rules!$E$4:$E$7,MATCH({T},Rules!$A$4:$A$7,0)),\"\")"),
    ]
    for rid, label, f in results:
        ws.cell(r, 1, rid).font = Font(name=F)
        ws.cell(r, 2, label).font = Font(name=F, bold=True)
        x = ws.cell(r, 3, f)
        x.font = Font(name=F, bold=True)
        x.fill = GREY
        for j in range(1, 5):
            ws.cell(r, j).border = BOX
            ws.cell(r, j).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    ws.cell(tier_row, 4, "Rules and thresholds: see the Rules tab (CyberCede defaults).").font = Font(name=F, size=9, color="555555")

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 52
    ws.column_dimensions["C"].width = 46
    ws.column_dimensions["D"].width = 48
    ws.freeze_panes = "A5"
    if example:
        ws.cell(3, 1, "Example values drawn from an internal BIA; confirm products used (A3) and AI use (B13).").font = Font(
            name=F, size=9, color="C00000")
    wb.save(path)
    print("saved", path)


if __name__ == "__main__":
    build(sys.argv[1], example=False)
    build(sys.argv[2], example=True)
