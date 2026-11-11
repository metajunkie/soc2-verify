"""
Deterministic verifier for AI-produced SOC 2 extractions.

The AI reads the report (any auditor, any format) and emits JSON that follows
schema/extraction.schema.json. This module checks that JSON against the source
PDF, without any AI, and writes a verification log a human or an auditor can
re-run and get the same answer.

Status levels per check:
  PASS    - verified
  REVIEW  - needs human judgment (not necessarily wrong)
  FAIL    - contradicted by the source, or a required condition not met
  INFO    - context only
"""

import datetime as dt
import json
import os
import re
from collections import Counter, defaultdict

from .pdftext import PdfDoc, normalize, sha256_file

PASS, REVIEW, FAIL, INFO = "PASS", "REVIEW", "FAIL", "INFO"
_ORDER = {FAIL: 0, REVIEW: 1, PASS: 2, INFO: 3}


class Results:
    def __init__(self):
        self.items = []

    def add(self, section, check, status, detail, refs=None):
        self.items.append({"section": section, "check": check, "status": status,
                           "detail": detail, "refs": refs or []})

    def overall(self):
        open_ = {i["status"] for i in self.items if not i.get("disposition")}
        decided = any(i.get("disposition") for i in self.items)
        if FAIL in open_:
            return FAIL
        if REVIEW in open_:
            return "PASS WITH ITEMS FOR REVIEW"
        return "PASS WITH DISPOSITIONED ITEMS" if decided else PASS

    def counts(self):
        return Counter(i["status"] for i in self.items)

    def apply_dispositions(self, dispositions):
        """Attach the reviewer's recorded decisions to matching FAIL/REVIEW items (matched by key)."""
        by_key = {d.get("key"): d for d in dispositions or [] if d.get("key") and d.get("decision")}
        used = set()
        for i in self.items:
            d = by_key.get(item_key(i))
            if d and i["status"] in (FAIL, REVIEW):
                i["disposition"] = d
                used.add(d["key"])
        for k in sorted(set(by_key) - used):
            self.add("Dispositions", "Disposition matches no current item", REVIEW,
                     f"'{k}' no longer matches a finding: the finding changed or was resolved. Review and remove it.")


def item_key(i):
    return f"{i['section']} :: {i['check']}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _iter_evidence(ex):
    """Yield (label, evidence_dict) for every evidence object in the extraction."""
    rep = ex.get("report", {})
    op = rep.get("opinion", {})
    if op.get("evidence"):
        yield "Opinion", op["evidence"]
    for i, ev in enumerate(rep.get("evidence", []) or [], 1):
        yield f"Report fact #{i}", ev
    for so in ex.get("subservice_orgs", []) or []:
        if so.get("evidence"):
            yield f"Subservice org: {so.get('name')}", so["evidence"]
    for i, c in enumerate(ex.get("cuecs", []) or [], 1):
        if c.get("evidence"):
            yield f"CUEC #{i}", c["evidence"]
    for c in ex.get("controls", []) or []:
        if c.get("evidence"):
            yield f"Control {c.get('id')}", c["evidence"]
    for e in ex.get("exceptions", []) or []:
        if e.get("evidence"):
            yield f"Exception {e.get('control_id')}", e["evidence"]


def _id_shape(cid):
    """'6.2.1' -> r'\\d{1,2}\\.\\d{1,2}\\.\\d{1,2}', 'A1.2.3' -> r'[A-Z]{1,3}\\d{1,2}\\.\\d{1,2}\\.\\d{1,2}'."""
    out = []
    for tok in re.findall(r"[A-Za-z]+|\d+|.", cid):
        if tok.isalpha():
            out.append("[A-Z]{1,3}")
        elif tok.isdigit():
            out.append(r"\d{1,3}")
        else:
            out.append(re.escape(tok))
    return "".join(out)


def _fmt_long_date(d):
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 31.0


RESULT_OK = re.compile(r"no (?:relevant )?(?:exceptions?|deviations?) (?:were |was )?(?:noted|identified|found)")
EXCEPTION_PATTERNS = [
    ("exception/deviation noted", re.compile(r"\b(?:exceptions?|deviations?) (?:was |were )?(?:noted|identified|found)\b")),
    ("not operating effectively", re.compile(r"not operating effectively|did not operate effectively")),
    ("'except for' (modified opinion wording)", re.compile(r"\bexcept for\b")),
    ("qualified / adverse opinion wording", re.compile(r"basis for (?:qualified|adverse)|\bqualified opinion\b|\badverse opinion\b")),
    ("disclaimer of opinion wording", re.compile(r"do not express an opinion|disclaimer of opinion")),
    ("management response (often accompanies exceptions)", re.compile(r"management'?s response")),
]
_DISMISS_PREFIX = re.compile(r"(?:no|\bno relevant|\bany|\bif|\bwhether|\bwithout)\s*$")   # "no" may be glued to the word before

SPECIFIC_TOKENS = [
    (r"aes-?\s?256", ["aes-256", "aes 256", "aes256", "advanced encryption standard"]),
    (r"\btls\b", ["tls", "transport layer security"]),
    (r"\bmfa\b|multi-?factor", ["mfa", "multi-factor", "multifactor", "two-factor", "2fa"]),
    (r"\bvpn\b", ["vpn", "virtual private network"]),
    (r"\bips\b", ["ips", "intrusion prevention"]),
    (r"\bids\b", ["ids", "intrusion detection"]),
    (r"\bkms\b", ["kms", "key management"]),
    (r"\bsso\b|single sign-?on", ["sso", "single sign"]),
    (r"24/7|24x7|24 x 7", ["24/7", "24x7", "24 x 7", "continuous"]),
    (r"\bdaily\b", ["daily"]),
    (r"\bweekly\b", ["weekly"]),
    (r"\bmonthly\b", ["monthly"]),
    (r"\bquarterly\b", ["quarter"]),
    (r"\bannual(?:ly)?\b", ["annual"]),
    (r"\b\d+\s*hours?\b", None),     # number-specific; checked literally
    (r"\b\d+\s*days?\b", None),
    (r"\bencrypt\w*", ["encrypt"]),
    (r"\bpenetration test", ["penetration"]),
    (r"\bbackground check", ["background"]),
    (r"\bgeographic", ["geographic", "region", "separate location", "offsite", "off-site"]),
]


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check_integrity(doc, ex, res):
    if doc.was_encrypted:
        res.add("Integrity", "PDF encryption", INFO, "PDF is encrypted; opened with the supplied/empty password.")
    if doc.no_text_pages:
        frac = len(doc.no_text_pages) / doc.n_pages
        res.add("Integrity", "Text layer", FAIL if frac > 0.2 else REVIEW,
                f"{len(doc.no_text_pages)} of {doc.n_pages} pages have no extractable text (scanned or image-only): "
                f"pages {doc.no_text_pages[:15]}. Run OCR (e.g. ocrmypdf) and re-verify, or review those pages manually.")
    src = ex.get("source", {})
    claimed = src.get("sha256")
    if claimed:
        if claimed.lower() == doc.sha256:
            res.add("Integrity", "Source PDF hash matches extraction", PASS, doc.sha256)
        else:
            res.add("Integrity", "Source PDF hash matches extraction", FAIL,
                    f"Extraction was made from a different file. Expected {claimed}, got {doc.sha256}")
    else:
        res.add("Integrity", "Source PDF hash recorded", INFO, f"SHA-256 {doc.sha256} (extraction did not record one)")
    pc = src.get("page_count")
    if pc and pc != doc.n_pages:
        res.add("Integrity", "Page count", FAIL, f"Extraction says {pc} pages; PDF has {doc.n_pages}")
    if doc.printed_offset is not None:
        res.add("Integrity", "Page numbering", INFO,
                f"Printed page numbers = PDF page - {doc.printed_offset}. Citations must say which one they use.")


def check_quotes(doc, ex, res):
    n = ok = 0
    for label, ev in _iter_evidence(ex):
        n += 1
        q, page = ev.get("quote", "") or "", ev.get("pdf_page")
        if not q.strip():
            heading = bool(TSC_HEADING.match(label.split()[-1])) if label.startswith("Control ") else False
            res.add("Evidence quotes", label, REVIEW if heading else FAIL,
                    f"No quote provided (cited PDF p.{page}). Evidence is missing; this is an omission, not invented text.")
            continue
        r = doc.find_quote(q, page)
        st = r["status"]
        if st in ("exact", "approximate"):
            ok += 1
            pp = ev.get("printed_page")
            exp_pp = doc.printed_page(page) if page else None
            if pp and exp_pp and pp != exp_pp:
                res.add("Evidence quotes", label, REVIEW,
                        f"Quote found on PDF p.{page}, but printed_page given as {pp} (expected {exp_pp}).")
            continue
        if st.endswith("other_page"):
            res.add("Evidence quotes", label, FAIL,
                    f"Quote is real but cited on PDF p.{page}; actually found on "
                    + ", ".join(doc.page_ref(p) for p in r["found_pages"][:5]),
                    [q[:160]])
        else:
            res.add("Evidence quotes", label, FAIL,
                    f"Quote NOT found in the report (best similarity {r['score']}). Possible fabrication or paraphrase.",
                    [q[:160]])
    if n:
        res.add("Evidence quotes", "Quotes verified on cited page", PASS if ok == n else FAIL, f"{ok} of {n}")
    else:
        res.add("Evidence quotes", "Quotes verified on cited page", REVIEW,
                "Extraction contains no verbatim quotes, so its facts can only be spot-checked by search (see below).")


def check_report_facts(doc, ex, res):
    rep = ex.get("report", {})
    for key, label in (("service_org", "Service organization"), ("auditor", "Auditor firm"),
                       ("system_name", "System name")):
        val = rep.get(key)
        if not val:
            res.add("Report facts", label, FAIL, "Missing from extraction")
            continue
        pages = doc.pages_containing(val)
        if not pages and key == "system_name":
            core = re.sub(r"\(.*?\)", "", val).strip()
            pages = doc.pages_containing(core)
        res.add("Report facts", f"{label}: '{val}'", PASS if pages else FAIL,
                ("appears on " + ", ".join(doc.page_ref(p) for p in pages[:4])) if pages
                else "value not found anywhere in the PDF")
    for key, label in (("period_start", "Period start"), ("period_end", "Period end"), ("issue_date", "Issue date")):
        d = _parse_date(rep.get(key))
        if not d:
            res.add("Report facts", label, FAIL, f"Missing/unparseable: {rep.get(key)!r}")
            continue
        variants = [_fmt_long_date(d), d.strftime("%m/%d/%Y"), d.strftime("%Y-%m-%d"), f"{d.month}/{d.day}/{d.year}",
                    f"{d.day} {d.strftime('%B')} {d.year}", f"{d.day:02d} {d.strftime('%B')} {d.year}",   # day-first: 01 August 2025
                    f"{d.strftime('%B')} {d.day:02d}, {d.year}"]
        pages = sorted({p for v in variants for p in doc.pages_containing(v)})
        res.add("Report facts", f"{label}: {d.isoformat()}", PASS if pages else FAIL,
                ("appears on " + ", ".join(doc.page_ref(p) for p in pages[:4])) if pages
                else "date not found in the PDF in any common format")


def check_opinion(doc, ex, res):
    claimed = (ex.get("report", {}).get("opinion", {}) or {}).get("type")
    pages = doc.pages_containing("in our opinion")
    if not pages:
        res.add("Opinion", "Locate opinion paragraph", REVIEW, "Could not find 'In our opinion' text; verify manually.")
        return
    flags = []
    for p in pages:
        t = doc.norm[p - 1]
        for name, pat in EXCEPTION_PATTERNS[2:5]:
            for m in pat.finditer(t):
                flags.append((p, name, doc.context(p, m.start(), m.end(), 80)))
    detected = "modified" if flags else "unqualified"
    where = ", ".join(doc.page_ref(p) for p in pages)
    if claimed == "unqualified" and not flags:
        res.add("Opinion", "Opinion is unqualified", PASS, f"No modifying language on opinion page(s): {where}")
    elif claimed == "unqualified" and flags:
        res.add("Opinion", "Opinion is unqualified", FAIL,
                "Extraction says unqualified, but modifying language found", [f"{doc.page_ref(p)}: ...{c}..." for p, _, c in flags])
    elif claimed and claimed != "unqualified" and flags:
        res.add("Opinion", f"Opinion is {claimed}", PASS, f"Modifying language found on {where}")
    elif claimed and claimed != "unqualified":
        res.add("Opinion", f"Opinion is {claimed}", REVIEW, f"Extraction says {claimed}; no modifying language found on {where}")
    else:
        res.add("Opinion", "Opinion type", FAIL, f"Extraction did not classify the opinion (detector: {detected})")


def check_exceptions(doc, ex, res):
    ok_count = sum(len(RESULT_OK.findall(t)) for t in doc.norm)
    hits, dismissed, scope_notes = [], [], []
    testing_pages = set(_control_pages(doc))
    for p, t in enumerate(doc.norm, 1):
        for name, pat in EXCEPTION_PATTERNS:
            for m in pat.finditer(t):
                prefix = t[max(0, m.start() - 20):m.start()]
                ctx = doc.context(p, m.start(), m.end(), 90)
                # Auditor-report boilerplate ("assessing the risks that ... controls ... did not operate
                # effectively") is not a finding. Only count it inside the testing tables.
                if name == "not operating effectively" and p not in testing_pages:
                    if re.search(r"risks? that|whether|responsibilit", t[max(0, m.start() - 250):m.start()]):
                        dismissed.append((p, name + " (auditor boilerplate)", ctx))
                        continue
                # Opinion-modifying wording only counts where the auditor gives the opinion.
                if name.startswith("'except for'") and "in our opinion" not in t and "basis for" not in t:
                    dismissed.append((p, name + " (not in the opinion section)", ctx))
                    continue
                if name.startswith("disclaimer"):
                    after = t[m.end():m.end() + 160]
                    if re.search(r"^.{0,80}\bon\b.{0,60}(other information|section v|complementary|subservice|user entit|management.s)", after):
                        dismissed.append((p, name + " (scope note on other information, not a disclaimer)", ctx))
                        continue
                    on = re.search(r"\bon (?:the )?(.{5,90}?)(?:\.|;|$)", after)
                    if on:   # "we do not express an opinion ... on X": the opinion stands, but X is outside it
                        scope_notes.append((p, on.group(1).strip(), ctx))
                        continue
                if name.startswith("exception/deviation") and p not in testing_pages and \
                        re.search(r"\b(where|if|any|whether)\b", t[max(0, m.start() - 45):m.start()]):
                    dismissed.append((p, name + " (methodology text outside the testing tables)", ctx))
                    continue
                if _DISMISS_PREFIX.search(prefix):
                    if not prefix.rstrip().endswith("no") and "no relevant" not in prefix:
                        dismissed.append((p, name, ctx))
                    continue
                hits.append((p, name, ctx))
    for p, what, ctx in scope_notes:
        res.add("Scope: auditor exclusions", f"No assurance on: {what[:70]}", REVIEW,
                f"{doc.page_ref(p)}: the auditor states it expresses no opinion or assurance on this. If you rely on "
                f"the vendor for it, this report doesn't cover it.", [f"...{ctx}..."])
    extracted = ex.get("exceptions", []) or []
    res.add("Exceptions (independent scan)", "Clean result statements counted in PDF", INFO,
            f"{ok_count} occurrences of 'no exceptions/deviations noted'")
    for p, name, ctx in dismissed:
        res.add("Exceptions (independent scan)", "Exception wording dismissed as non-result", INFO,
                f"{doc.page_ref(p)}: '{name}' is descriptive wording, not a test result",
                [f"...{ctx}..."])
    if not hits and not extracted:
        res.add("Exceptions (independent scan)", "Scanner and extraction agree: no exceptions", PASS,
                "No exception or modified-opinion wording found anywhere in the report")
        return
    ex_pages = {(e.get(k) or {}).get("pdf_page") for e in extracted
                for k in ("evidence", "management_response_evidence")} - {None}
    # a deviation's write-up and management's response often run onto the next page
    unmatched = [h for h in hits if not any(abs(h[0] - q) <= 1 for q in ex_pages)]
    for p, name, ctx in unmatched:
        res.add("Exceptions (independent scan)", "Exception wording not in extraction", REVIEW,
                f"{doc.page_ref(p)}: {name}", [f"...{ctx}..."])
    if extracted and not hits:
        res.add("Exceptions (independent scan)", "Extraction lists exceptions the scanner did not find", REVIEW,
                f"{len(extracted)} listed; confirm wording on cited pages")
    if hits and not unmatched:
        res.add("Exceptions (independent scan)", "All scanner hits are covered by the extraction", PASS, f"{len(hits)} hits")


RESULT_LINE = re.compile(r"(?mi)^\s*no (?:relevant )?(?:exceptions?|deviations?) (?:were |was )?(?:noted|identified|found)\.?\s*$")


def _control_pages(doc):
    """Pages of the testing tables: those with a stand-alone result line ("No exceptions noted.").
    A phrase like "Tests Performed" on the cover or in the opinion doesn't count."""
    pages = [i for i in range(1, doc.n_pages + 1) if RESULT_LINE.search(doc.raw[i - 1])]
    if not pages:
        return [i for i, t in enumerate(doc.norm, 1) if RESULT_OK.search(t) or "tests performed" in t]
    # The testing section is one block. Take the largest cluster of result pages and fill the gaps: a page
    # holding only a deviation ("Deviation noted.") has no clean result line but is the page that matters most.
    clusters, cur = [], [pages[0]]
    for p in pages[1:]:
        if p - cur[-1] <= 4:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)
    main = max(clusters, key=len)
    return list(range(main[0], main[-1] + 1))


TSC_HEADING = re.compile(r"^(CC|PI|A|C|P)\d{1,2}\.\d{1,2}$")


def check_completeness(doc, ex, res, cited_ids):
    """Reconcile control IDs and test results the AI reported against what is in the PDF."""
    pages = _control_pages(doc)
    if not pages:
        res.add("Completeness", "Locate control testing section", REVIEW, "Could not find testing tables")
        return
    synthetic = [c for c in ex.get("controls", []) or [] if c.get("id_source") == "synthetic" or "#" in str(c.get("id", ""))]
    if synthetic:
        res.add("Completeness", "Report does not number its controls", INFO,
                f"{len(synthetic)} controls identified by criterion and position (e.g. {synthetic[0].get('id')}); "
                f"reconciled by test results rather than control IDs")
    raw_ids = [str(c.get("id")).strip() for c in ex.get("controls", []) or [] if c.get("id") and c not in synthetic]
    raw_ids = [re.split(r"\s+[-\u2013:]\s*|\s", i, maxsplit=1)[0] for i in raw_ids]   # "CC3.1 - COSO..." -> "CC3.1"
    # A two-level ID with a TSC prefix (CC1.2, A1.1, C1.2) is a criterion heading, not a vendor control.
    heads = sorted({i for i in raw_ids if TSC_HEADING.match(i)}, key=_natkey)
    ex_ids = [i for i in raw_ids if not TSC_HEADING.match(i)]
    if heads:
        res.add("Completeness", "Criterion headings listed as controls", REVIEW,
                f"{len(heads)} entries are TSC criterion headings, not the vendor's controls (excluded from the "
                f"control count): {', '.join(heads[:30])}")
    sample_ids = ex_ids or sorted(cited_ids)
    if not sample_ids and not synthetic:
        res.add("Completeness", "Control reconciliation", FAIL, "Extraction lists no controls and cites none")
        return
    shapes = sorted({_id_shape(i) for i in sample_ids})
    bare = [sh for sh in shapes if sh == r"\d{1,3}"]          # "42. The organization..." needs its dot
    other = [sh for sh in shapes if sh != r"\d{1,3}"]
    alts = ([r"(?:" + "|".join(other) + r")(?=\.?\s)"] if other else []) + ([r"\d{1,3}(?=\.\s)"] if bare else [])
    pat = re.compile(r"^\s*(" + "|".join(alts) + r")", re.M) if alts else re.compile(r"(?!x)x")
    found = defaultdict(list)
    for p in pages:
        for m in pat.finditer(doc.raw[p - 1]):
            found[m.group(1)].append(p)
    pdf_ids = {i for i in found if not TSC_HEADING.match(i)}
    rng = f"PDF p.{pages[0]}-{pages[-1]}"
    res.add("Completeness", "Control IDs found in testing section", INFO, f"{len(pdf_ids)} unique control IDs ({rng})")

    if ex_ids or synthetic:
        missing = sorted(pdf_ids - set(ex_ids), key=_natkey)
        extra = sorted(set(ex_ids) - pdf_ids, key=_natkey)
        if ex_ids:
            res.add("Completeness", "Every control in the report was extracted",
                    PASS if not missing else FAIL,
                    f"{len(pdf_ids) - len(missing)} of {len(pdf_ids)} extracted" + (f"; missing: {', '.join(missing[:30])}" if missing else ""))
        if extra:
            res.add("Completeness", "Extracted control IDs not found in report", FAIL, ", ".join(extra[:30]))
        n_tests = sum(len(c.get("tests", []) or []) for c in ex.get("controls", []))
        n_results = sum(len(RESULT_OK.findall(doc.norm[p - 1])) for p in pages)
        all_tests = [t for c in ex.get("controls", []) for t in c.get("tests", []) or []]
        n_ex_tests = sum(1 for t in all_tests if t.get("result") == "exception")
        n_nt = sum(1 for t in all_tests if t.get("result") == "not_tested")
        n_clean = sum(1 for t in all_tests if t.get("result") == "no_exceptions")
        res.add("Completeness", "Test results reconcile to PDF",
                PASS if n_clean == n_results else REVIEW,
                f"extraction: {n_tests} tests ({n_clean} clean, {n_ex_tests} with exceptions, {n_nt} not tested); "
                f"PDF: {n_results} clean result statements")
    else:
        unknown = sorted(set(cited_ids) - pdf_ids, key=_natkey)
        cov = len(pdf_ids & set(cited_ids))
        res.add("Completeness", "Controls cited by the form vs controls in report", INFO,
                f"form cites {cov} of {len(pdf_ids)} tested controls (a mapped form is not expected to cite all)")
        if unknown:
            res.add("Completeness", "Cited control IDs that do not exist in report", FAIL, ", ".join(unknown[:30]))
    return found


def _natkey(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


def check_criteria(doc, ex, res, profile, criteria):
    pages = _control_pages(doc)
    text = "\n".join(doc.raw[p - 1] for p in pages)
    crit_pat = re.compile(r"(?<![A-Za-z])(CC\d\.\d|PI1\.\d|A1\.\d|C1\.\d|P\d\.\d)(?![\d.]*\d)")
    present = set(crit_pat.findall(text))
    all_crit = criteria["criteria"]
    prefixes = criteria["categories"]
    claimed = set(ex.get("report", {}).get("tsc_categories_in_scope", []) or [])
    required = set((profile or {}).get("required_tsc_categories", []) or []) | {"Security"}

    for cat in sorted(required):
        if cat not in claimed:
            res.add("Scope: TSC categories", f"{cat} in scope", FAIL,
                    f"Client requires {cat}; extraction says report covers {sorted(claimed) or 'nothing'}")
        else:
            res.add("Scope: TSC categories", f"{cat} in scope", PASS, "required by profile; in report scope")

    for cat in sorted(set((profile or {}).get("recommended_tsc_categories", []) or []) - required):
        if cat in claimed:
            res.add("Scope: TSC categories", f"{cat} in scope (recommended)", PASS, "recommended by profile; in report scope")
        else:
            res.add("Scope: TSC categories", f"{cat} in scope (recommended)", REVIEW,
                    f"Profile recommends {cat}, but the report doesn't include it. Decide whether that matters for this use.")

    for cat in sorted(claimed | required):
        pref = prefixes.get(cat)
        if not pref:
            continue
        expected = [c for c in all_crit if re.match(rf"^{pref}\d", c) and not (pref == "C" and c.startswith("CC"))
                    and not (pref == "P" and c.startswith("PI"))]
        missing = [c for c in expected if c not in present]
        declared_na, truly_missing = [], []
        for c in missing:
            na_pages = [p for p, t in enumerate(doc.norm, 1)
                        if re.search(rf"(not applicable|non-applicable|carved out|carve-out)[^.]{{0,200}}\b{re.escape(c.lower())}\b"
                                     rf"|\b{re.escape(c.lower())}\b[^.]{{0,200}}(not applicable|non-applicable|carved out)", t)]
            (declared_na if na_pages else truly_missing).append((c, na_pages))
        st = PASS if not missing else (FAIL if truly_missing and cat in required else REVIEW)
        res.add("Coverage: Trust Services Criteria", f"{cat}: all criteria addressed",
                st, f"{len(expected) - len(missing)} of {len(expected)} criteria have tested controls"
                + (f"; not found: {', '.join(c for c, _ in truly_missing)}" if truly_missing else ""))
        for c, pg in declared_na:
            lab = all_crit.get(c, "")
            lab = (lab.get("text", "")[:70] + "...") if isinstance(lab, dict) else lab
            res.add("Coverage: Trust Services Criteria", f"{c} ({lab}) not tested", REVIEW,
                    f"Report declares it not applicable / carved out on {', '.join(doc.page_ref(p) for p in pg[:3])}. "
                    f"Confirm this is acceptable for how the client uses the vendor (who covers it?).")


_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE_RX = re.compile(rf"((?:{_MONTHS}) \d{{1,2}}, \d{{4}}|\d{{1,2}} (?:{_MONTHS}) \d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})")


def _bridge_letters(doc, period_end):
    """Bridge (gap) letters saved next to the report: the latest date they mention after the period end is
    taken as the date they cover through. Only letters that mention the report's period end count."""
    import glob
    out = []
    folder = os.path.dirname(doc.path)
    for f in sorted(glob.glob(os.path.join(folder, "*.pdf"))):
        name = os.path.basename(f).lower()
        if os.path.abspath(f) == os.path.abspath(doc.path) or not any(k in name for k in ("bridge", "gap letter", "comfort letter")):
            continue
        try:
            from pypdf import PdfReader
            r = PdfReader(f)
            if r.is_encrypted:
                r.decrypt("")
            text = re.sub(r"\s+", " ", " ".join((pg.extract_text() or "") for pg in r.pages))
        except Exception:  # noqa: BLE001
            continue
        dates = [d for d in (_parse_date(x) for x in _DATE_RX.findall(text)) if d]
        if period_end not in dates:
            continue                                   # not a letter for this report
        later = [d for d in dates if d > period_end]
        if not later:
            continue
        out.append({"file": os.path.basename(f), "covered_through": max(later),
                    "dated": max(later).isoformat(),
                    "no_material_changes": bool(re.search(r"no (?:material|significant) changes", text, re.I))})
    return out


def check_dates(doc, ex, res, profile, review_date):
    rep = ex.get("report", {})
    end = _parse_date(rep.get("period_end"))
    start = _parse_date(rep.get("period_start"))
    if start and end:
        months = _months_between(start, end)
        res.add("Dates", "Type II period length", PASS if months >= 5.5 else REVIEW,
                f"{start} to {end} ({months:.1f} months)")
    if not end:
        return
    age = _months_between(end, review_date)
    max_age = (profile or {}).get("max_report_age_months", 12)
    bridge = (profile or {}).get("bridge_letter_after_months", 3)
    res.add("Dates", "Report is current", PASS if age <= max_age else FAIL,
            f"period ended {end}; {age:.1f} months before review date {review_date} (limit {max_age})")
    covered, letters = end, _bridge_letters(doc, end)
    for lt in letters:
        res.add("Dates", f"Bridge letter: {lt['file']}", INFO if lt["no_material_changes"] else REVIEW,
                f"covers through {lt['covered_through']}" + (f", dated {lt['dated']}" if lt.get("dated") else "")
                + ("; states no material changes" if lt["no_material_changes"]
                   else "; does NOT clearly state 'no material changes': read it"))
        covered = max(covered, lt["covered_through"])
    gap = _months_between(covered, review_date)
    if gap > bridge:
        res.add("Dates", "Bridge (gap) letter", REVIEW,
                f"{gap:.1f} months of uncovered time since {'the bridge letter' if letters else 'period end'} "
                f"(> {bridge}). Request {'an updated' if letters else 'a'} bridge letter or confirm one isn't needed for this use.")
    elif letters:
        res.add("Dates", "Bridge (gap) letter", PASS,
                f"bridge letter covers through {covered}; {gap:.1f} months uncovered (within {bridge})")


def check_scope(doc, ex, res, profile):
    if not profile:
        res.add("Scope: services used", "Scope profile", REVIEW, "No scope profile supplied, so scope could not be checked")
        return
    for svc in profile.get("services_used", []) or []:
        alts = [x.strip() for x in str(svc).split("|") if x.strip()]
        pages = sorted({p for alt in alts for p in doc.pages_containing(alt)})
        res.add("Scope: services used", f"'{svc}' described in report", PASS if pages else REVIEW,
                ("appears on " + ", ".join(doc.page_ref(p) for p in pages[:4])) if pages
                else "is listed in your scope profile as a service you use, but the report never mentions it. Confirm "
                     "with the vendor whether it is in the audited system; if not, this report gives no assurance for it.")
    # Is there a TESTED vendor-oversight control over subservice orgs (e.g. annual review of their SOC reports)?
    oversight = []
    pat = re.compile(r"(service auditor|soc) reports?[^.]{0,200}subservice organi[sz]ations?|subservice organi[sz]ations?[^.]{0,200}"
                     r"(soc|service auditor) reports?[^.]{0,120}review")
    for p in _control_pages(doc):
        m = pat.search(doc.norm[p - 1])
        if m and RESULT_OK.search(doc.norm[p - 1][m.start():m.end() + 1500]):
            oversight.append(p)
    for so in ex.get("subservice_orgs", []) or []:
        name = so.get("name", "")
        found = doc.pages_containing(name)
        if not found:
            res.add("Subservice orgs", name, FAIL, "name not found in report")
            continue
        if so.get("method") == "carve-out":
            sens = set(profile.get("data_types", []) or []) - {"none"}
            if oversight:
                res.add("Subservice orgs", f"{name} (carved out)", INFO,
                        "Controls at this provider are not tested in this report, but the vendor's oversight of its "
                        "subservice organizations (review of their SOC reports) was tested with no exceptions on "
                        + ", ".join(doc.page_ref(p) for p in oversight[:2]) + ". Treat topics under the criteria this "
                        "provider is responsible for as covered by the subcontractor (CAIQ: 3rd-party outsourced).")
            else:
                res.add("Subservice orgs", f"{name} (carved out)", REVIEW if sens else INFO,
                        "Controls at this provider are NOT tested in this report, and no tested vendor-oversight control "
                        "was found. " + ("Client data types " + ", ".join(sorted(sens)) + " may reside there: confirm the "
                                         "vendor monitors it, or obtain the provider's SOC report." if sens else ""))


def check_claims(doc, ex, res, found_ids):
    """Verify free-text claims: cited control IDs exist on cited pages; specifics appear there."""
    claims = ex.get("claims", []) or []
    if not claims:
        return set()
    cited_all = set()
    page_misses = unsupported = 0
    conv = Counter()
    for cl in claims:
        stmt = cl.get("statement", "")
        cpages, cids = set(), set()
        for c in cl.get("citations", []) or []:
            cpages.update(c.get("pdf_pages", []) or [])
            cids.update(c.get("control_ids", []) or [])
        cited_all |= cids
        # 1) do cited IDs appear on cited pages?
        for cid in sorted(cids, key=_natkey):
            actual = found_ids.get(cid, []) if found_ids else []
            if not actual:
                actual = [p for p in range(1, doc.n_pages + 1) if re.search(rf"(?<![\w.]){re.escape(cid)}(?![\w.]*\d)", doc.raw[p - 1])]
            if not cpages:
                continue
            if set(actual) & cpages:
                conv["pdf"] += 1
            elif any(doc.pdf_page_from_printed(p) in actual for p in cpages):
                conv["printed"] += 1
            else:
                page_misses += 1
                res.add("Claims: citations", f"{cl.get('field')}: control {cid}", FAIL,
                        f"cited on p.{sorted(cpages)} but appears on "
                        + (", ".join(doc.page_ref(p) for p in actual[:4]) if actual else "no page"))
        # 2) are specific details in the statement supported by the cited pages?
        search_pages = set(cpages)
        for cid in cids:
            search_pages |= set(found_ids.get(cid, []) if found_ids else [])
        corpus = " ".join(doc.norm[p - 1] for p in search_pages if 1 <= p <= doc.n_pages)
        if not corpus:
            continue
        low = stmt.lower()
        missing = []
        for pat, syns in SPECIFIC_TOKENS:
            for m in re.finditer(pat, low):
                tok = m.group(0)
                if syns is None:
                    if normalize(tok) not in corpus:
                        missing.append(tok)
                elif not any(s in corpus for s in syns):
                    missing.append(tok)
        if missing:
            unsupported += 1
            for tok in sorted(set(missing)):
                syn = next((s for pat, s in SPECIFIC_TOKENS if s and re.fullmatch(pat, tok)), None) or [tok]
                elsewhere = sorted({p for s in syn for p in doc.pages_containing(rf"\b{re.escape(s)}", regex=True)})
                testing = set(_control_pages(doc))
                if elsewhere and not (set(elsewhere) & testing):
                    msg = (f"'{tok}' appears only in descriptive sections ("
                           + ", ".join(doc.page_ref(p) for p in elsewhere[:4])
                           + "), not in any tested control. Described is not tested: the claim may overstate assurance.")
                    st = REVIEW
                elif elsewhere:
                    msg = (f"'{tok}' is not on the cited pages but appears elsewhere: "
                           + ", ".join(doc.page_ref(p) for p in elsewhere[:4]) + ". Fix the citation.")
                    st = REVIEW
                else:
                    msg = f"'{tok}' appears NOWHERE in the report. Unsupported detail added by the AI."
                    st = FAIL
                res.add("Claims: specific details", cl.get("field", "?"), st, msg, [stmt[:200]])
    if conv:
        which = "PDF page index" if conv["pdf"] >= conv["printed"] else "printed page numbers"
        res.add("Claims: citations", "Page-number convention used by citations", INFO,
                f"{which} (pdf={conv['pdf']}, printed={conv['printed']}). Readers of the printed report must be told.")
    res.add("Claims: citations", "Cited controls located on cited pages", PASS if not page_misses else FAIL,
            f"{page_misses} citation(s) point to the wrong page" if page_misses else "all cited controls found on cited pages")
    res.add("Claims: specific details", "Specific details supported by cited pages",
            PASS if not unsupported else REVIEW, f"{unsupported} of {len(claims)} claims contain unsupported specifics")
    return cited_all


# ---------------------------------------------------------------------------
def ccm_overlay(doc, ex, res, crosswalk):
    """Express this SOC 2 report's coverage in CSA CCM terms, using AICPA's TSC-to-CCM mapping.
    A CCM control is 'covered' only if every TSC criterion it maps to has tested controls in this report."""
    pages = _control_pages(doc)
    text = "\n".join(doc.raw[p - 1] for p in pages)
    present = set(re.findall(r"(?<![A-Za-z])(CC\d\.\d|PI1\.\d|A1\.\d|C1\.\d|P\d\.\d)(?![\d.]*\d)", text))
    rows, counts = [], Counter()
    ctrls = crosswalk.get("ccm_controls", {})
    c2t = crosswalk.get("ccm_to_tsc", {})
    for cid in sorted(ctrls, key=lambda k: (k[:3], int(k[-2:]))):
        tscs = c2t.get(cid, [])
        if not tscs:
            st = "no TSC mapping"
        else:
            hit = [t for t in tscs if t in present]
            st = "mapped criteria tested" if len(hit) == len(tscs) else ("some mapped criteria tested" if hit else "mapped criteria not tested")
        counts[st] += 1
        rows.append({"ccm_id": cid, "title": ctrls[cid].get("title"), "domain": ctrls[cid].get("domain"),
                     "tsc": tscs, "status": st})
    res.add("Overlay: CSA CCM", "CCM coverage via AICPA mapping", INFO,
            f"{counts['mapped criteria tested']} with all mapped criteria tested, {counts['some mapped criteria tested']} some, "
            f"{counts['mapped criteria not tested']} none, {counts['no TSC mapping']} with no TSC mapping; "
            f"of {len(rows)} CCM controls. Mapping: TSC {crosswalk.get('mapping_versions', {}).get('Trust Services Criteria', '')} "
            f"to CCM {crosswalk.get('mapping_versions', {}).get('CSA CCM', '')}; current CCM {crosswalk.get('current_ccm_source')}.")
    if crosswalk.get("mapped_ids_not_in_current_ccm"):
        res.add("Overlay: CSA CCM", "Mapping vs. current CCM release", INFO,
                "Mapped IDs whose text changed too much to match automatically: "
                + ", ".join(crosswalk["mapped_ids_not_in_current_ccm"]) + ". Their successors show as unmapped; map by hand if the client needs them.")
    return rows


def write_ccm_appendix(rows, path, vendor):
    order = ["mapped criteria tested", "some mapped criteria tested", "mapped criteria not tested", "no TSC mapping"]
    L = [f"# CSA CCM coverage from the SOC 2 report: {vendor}", "",
         "Derived using AICPA's 'Mapping: 2017 Trust Services Criteria to CSA's Cloud Controls Matrix'.", "",
         "**Read this as a triage view, not a CCM assessment.** 'Mapped criteria tested' means every TSC criterion the "
         "CCM control maps to has *some* tested control in the report, not that the report tests what the CCM control "
         "asks for. Example: CCM key-management controls map to CC6.1, which a report can satisfy with MFA and access "
         "controls while never testing key management. Use the 'not tested' and 'no mapping' lists as definite gaps, and "
         "spot-check the 'tested' list against the actual controls.", "",
         "| Domain | All mapped criteria tested | Some | None | No TSC mapping |", "|---|---|---|---|---|"]
    doms = {}
    for r in rows:
        doms.setdefault(r["domain"], Counter())[r["status"]] += 1
    for d, c in doms.items():
        L.append(f"| {d} | " + " | ".join(str(c[o]) for o in order) + " |")
    for o in order[1:]:
        sub = [r for r in rows if r["status"] == o]
        if sub:
            L += ["", f"## {o[0].upper() + o[1:]} ({len(sub)})", "", "| CCM | Title | Maps to TSC |", "|---|---|---|"]
            L += [f"| {r['ccm_id']} | {r['title']} | {', '.join(r['tsc']) or 'none'} |" for r in sub]
    open(path, "w").write("\n".join(L) + "\n")


def check_extractor_notes(ex, res):
    """Whatever the extractor itself couldn't resolve must reach the reviewer, not sit in a JSON field."""
    x = ex.get("extractor", {}) or {}
    for u in x.get("unresolved", []) or []:
        res.add("Extraction", f"Unresolved: {u.get('item', '?')}", REVIEW,
                "; ".join(u.get("problems", []) or [])[:300] or "the extractor could not verify this item")
    gate = (x.get("stats", {}) or {}).get("layout_gate")
    if gate:
        res.add("Extraction", "Parser layout gate", PASS if gate.get("status") == "PASS" else REVIEW,
                gate.get("detail", "") + ("" if gate.get("status") == "PASS"
                                          else ". Parsed controls are unvalidated for this report: review by hand."))


def verify(pdf_path, extraction, profile=None, criteria=None, review_date=None, password=None):
    review_date = review_date or dt.date.today()
    doc = PdfDoc(pdf_path, password)
    res = Results()
    check_extractor_notes(extraction, res)
    check_integrity(doc, extraction, res)
    check_quotes(doc, extraction, res)
    check_report_facts(doc, extraction, res)
    check_opinion(doc, extraction, res)
    check_exceptions(doc, extraction, res)
    cited = set()
    for cl in extraction.get("claims", []) or []:
        for c in cl.get("citations", []) or []:
            cited.update(c.get("control_ids", []) or [])
    found = check_completeness(doc, extraction, res, cited) or {}
    check_claims(doc, extraction, res, found)
    if criteria:
        check_criteria(doc, extraction, res, profile, criteria)
    check_dates(doc, extraction, res, profile, review_date)
    check_scope(doc, extraction, res, profile)
    return doc, res


def write_report(doc, res, extraction, profile, out_md, out_json, extraction_path, review_date, tool_version):
    items = sorted(res.items, key=lambda i: (_ORDER[i["status"]], i["section"]))
    c = res.counts()
    ext_hash = sha256_file(extraction_path) if extraction_path else None
    meta = {
        "tool": f"soc2-verify verifier {tool_version}",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "review_date": str(review_date),
        "source_pdf": doc.path, "source_pdf_sha256": doc.sha256, "pages": doc.n_pages,
        "extraction_file": extraction_path, "extraction_sha256": ext_hash,
        "extractor": extraction.get("extractor"),
        "profile": profile,
        "overall": res.overall(), "counts": dict(c),
    }
    with open(out_json, "w") as f:
        json.dump({"meta": meta, "results": items}, f, indent=2, default=str)

    rep = extraction.get("report", {})
    L = []
    L.append(f"# SOC 2 Verification Report: {rep.get('service_org', '?')}")
    L.append("")
    L.append(f"**Overall: {res.overall()}**  ·  FAIL {c.get(FAIL,0)}  ·  REVIEW {c.get(REVIEW,0)}  ·  PASS {c.get(PASS,0)}  ·  INFO {c.get(INFO,0)}")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Review date | {review_date} |")
    L.append(f"| Source PDF | `{doc.path.split('/')[-1]}` ({doc.n_pages} pages) |")
    L.append(f"| Source SHA-256 | `{doc.sha256}` |")
    L.append(f"| Extraction | `{(extraction_path or '').split('/')[-1]}` |")
    L.append(f"| Extraction SHA-256 | `{ext_hash}` |")
    exr = extraction.get("extractor") or {}
    L.append(f"| Extracted by | {exr.get('model','?')} via {exr.get('tool','?')} at {exr.get('run_at','?')} |")
    if profile:
        L.append(f"| Scope profile | {profile.get('client','?')}: {', '.join(profile.get('services_used', []) or [])} |")
    L.append(f"| Verifier | {meta['tool']} (deterministic, no AI) |")
    L.append("")
    attention = [i for i in items if i["status"] in (FAIL, REVIEW)]
    L.append("## Items for the reviewer")
    L.append("")
    if not attention:
        L.append("None.")
    for i in attention:
        d = i.get("disposition")
        tag = f"{i['status']} → {str(d['decision']).upper()}" if d else i["status"]
        L.append(f"- **{tag}** · {i['section']} · {i['check']}: {i['detail']}")
        for r in i["refs"][:3]:
            L.append(f"  - `{r}`")
        if d:
            L.append(f"  - *Disposition by {d.get('decided_by', '?')} on {d.get('date', '?')}:* {d.get('rationale', '')}"
                     + (f" Evidence: {d['evidence']}." if d.get("evidence") else "")
                     + (f" Revisit: {d['revisit']}." if d.get("revisit") else ""))
    L.append("")
    L.append("## All checks")
    L.append("")
    L.append("| Status | Section | Check | Detail |")
    L.append("|---|---|---|---|")
    for i in items:
        d = str(i["detail"]).replace("|", "/").replace("\n", " ")
        L.append(f"| {i['status']} | {i['section']} | {i['check'].replace('|','/')} | {d} |")
    L.append("")
    L.append("---")
    L.append("Generated by deterministic code. It verifies the AI extraction against the source PDF; re-running it on the "
             "same two files (see hashes) yields the same result. It does **not** make a risk decision: approval rests "
             "with the named reviewer.")
    with open(out_md, "w") as f:
        f.write("\n".join(L) + "\n")
    return meta
