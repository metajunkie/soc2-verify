"""
Code-first parser for SOC 2 control-testing tables (Section IV).

No AI. Reads the PDF text layer and turns the testing tables into the extraction schema's `controls`
list: control ID, the criterion heading it is listed under, the description, each test procedure, and
its verbatim result wording. Every control's evidence quote is copied from the page text, so it is
verbatim by construction.

What it can't parse cleanly, it reports instead of guessing (`issues`). Those items, plus any
in-scope criterion with no parsed control, are handed to the AI as targeted questions by
extract_hybrid.py.

Built against the common "Control | Activity | Tests Performed | Results" layout. Other auditors'
layouts will produce more `issues`, which is the point: the AI handles the leftovers.
"""

import re
from collections import OrderedDict

# Bump on every change to parsing behavior, and record why in CHANGELOG.md. run_regression.py must pass first.
PARSER_VERSION = "2026.11.11-2"
# Automatic gate: if the parser's clean-result count differs from the independent count by more than this,
# the layout is treated as unvalidated for this report (see PARSER_METHOD.md).
GATE_TOLERANCE = 0.02

# Criterion heading: "CC1.2 - COSO Principle 2: ..." / "A1.3 - The entity ..." (two-level ID + dash)
HEADING = re.compile(r"^\s*((?:CC|PI|A|C|P)\d{1,2}\.\d{1,2})\s*[-–—:]\s*(.*)$")
# Vendor control ID at the start of a line: "1.2.3 ...", "A1.2.3 ...", "CC6.1.2 ..."
# (the description may start on the same line as the ID, or on the next line)
CONTROL = re.compile(r"^\s*((?:[A-Z]{1,3})?\d{1,2}\.\d{1,2}\.\d{1,2}[a-z]?)(?:\s+(\S.*))?\s*$")
# Lines that begin a test procedure
TEST_START = re.compile(
    r"^\s*(inspected|inquired|observed|reperformed|re-performed|selected|for a (?:sample|selection)|"
    r"obtained|examined|reviewed|compared|traced|tested|corroborated|verified|determined|"
    r"for (?:the|each|all)\b|performed|recalculated)\b", re.I)
RESULT_OK = re.compile(r"no (?:relevant )?(?:exceptions?|deviations?) (?:were |was )?(?:noted|identified|found)", re.I)
RESULT_BAD = re.compile(r"\b(exceptions? (?:was |were )?noted|deviations? (?:was |were )?noted|"
                        r"the following exception|did not operate|was not operating|not operating effectively|"
                        r"exception:|deviation:)", re.I)
RESULT_NT = re.compile(r"\b(not tested|no testing (?:was )?performed|did not occur|no instances|unable to test|"
                       r"control did not operate during|not applicable)\b", re.I)
# Column gap inside one line: "...encrypted at rest.  Inspected evidence ..."
INLINE_TEST = re.compile(r"\s{2,}(?=(?:Inspected|Inquired|Observed|Reperformed|Re-performed|Selected|For a sample|"
                         r"For a selection|Obtained|Examined|Reviewed|Compared|Traced|Tested|Corroborated)\b)")
# Page furniture to drop
NOISE = [re.compile(p, re.I) for p in (
    r"^\s*$", r"^\s*control\s+(?:key\s+)?control activity", r"tests performed\s+results\s*$",
    r"^\s*downloaded (?:at|by)\b", r"soc 2 type i", r"^\s*\d{1,3}\s*$",
    # a repeated table header split one column per line
    r"^\s*(control|key control activity|control activity|tests performed|results|results of tests)\s*$",
    r"^\s*\d+\.0\s*[–—-]\s", r"^\s*page \d+")]


def _noise(line):
    return any(p.search(line) for p in NOISE)


def _clean(s):
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+([.,;:])", r"\1", s)


def _quote(words, n=24):
    w = words.split()
    return " ".join(w[:n])


def parse(doc, pages):
    """Return {"controls": [...], "issues": [...], "criteria_pages": {crit: [pages]}, "results": {...}}."""
    controls = OrderedDict()
    issues = []
    criteria_pages = {}
    cur_crit = None
    cur = None          # current control dict (internal form)
    heading_open = False

    def close_test():
        if cur and cur["_proc"]:
            cur["tests"].append({"procedure": _clean(" ".join(cur["_proc"])), "result": None, "result_text": ""})
            cur["_proc"] = []

    def finish():
        nonlocal cur
        if not cur:
            return
        if cur["_proc"]:   # trailing procedure without a result
            cur["tests"].append({"procedure": _clean(" ".join(cur["_proc"])), "result": None, "result_text": ""})
            cur["_proc"] = []
        cid = cur["id"]
        if cid in controls:          # same ID listed again (e.g. under another criterion): merge
            prev = controls[cid]
            for c in cur["criteria"]:
                if c not in prev["criteria"]:
                    prev["criteria"].append(c)
        else:
            controls[cid] = cur
        cur = None

    for p in pages:
        for line in doc.raw[p - 1].splitlines():
            if _noise(line):
                continue
            h = HEADING.match(line)
            if h and h.group(1).endswith(".0"):      # section title ("A1.0 - Additional Criteria..."), not a criterion
                finish()
                continue
            if h and not CONTROL.match(line):
                finish()
                cur_crit = h.group(1)
                criteria_pages.setdefault(cur_crit, [])
                if p not in criteria_pages[cur_crit]:
                    criteria_pages[cur_crit].append(p)
                heading_open = True
                continue
            m = CONTROL.match(line)
            if m:
                finish()
                heading_open = False
                first, *rest = INLINE_TEST.split(m.group(2) or "", maxsplit=1)
                cur = {"id": m.group(1), "criteria": [cur_crit] if cur_crit else [], "_desc": [first],
                       "_proc": [r.strip() for r in rest], "tests": [], "_page": p, "_pages": [p],
                       "_mode": "test" if rest else "desc"}
                if cur_crit and p not in criteria_pages[cur_crit]:
                    criteria_pages[cur_crit].append(p)
                continue
            if cur is None:
                if heading_open:     # wrapped criterion heading text
                    continue
                continue             # text before the first control on a page we can't place
            if p not in cur["_pages"]:
                cur["_pages"].append(p)
                if cur_crit and p not in criteria_pages.get(cur_crit, []):
                    criteria_pages.setdefault(cur_crit, []).append(p)
            text = line.strip()
            res = None
            short = len(text.split()) <= 8
            if cur["_mode"] in ("test", "after_result") or (cur["_mode"] == "desc" and short):
                if RESULT_OK.search(text) and short:
                    res = "no_exceptions"
                elif cur["_mode"] != "desc" and RESULT_BAD.search(text):
                    res = "exception"
                elif cur["_mode"] == "test" and RESULT_NT.search(text):
                    res = "not_tested"
            if res:
                if cur["_mode"] == "desc":       # result with no detected procedure start
                    issues.append({"id": cur["id"], "page": p, "problem": "result found before any test procedure"})
                if res != "no_exceptions":
                    issues.append({"id": cur["id"], "page": p, "problem": f"non-standard result ({res}): AI to read the wording",
                                   "kind": res})
                cur["tests"].append({"procedure": _clean(" ".join(cur["_proc"])), "result": res, "result_text": _clean(text)})
                cur["_proc"] = []
                cur["_mode"] = "after_result" if res == "no_exceptions" else "result_text"
                continue
            if cur["_mode"] == "result_text" and cur["tests"] and not (TEST_START.match(text) and text[:1].isupper()):
                # an exception or "not tested" result usually runs on: detail, management's response
                cur["tests"][-1]["result_text"] = _clean(cur["tests"][-1]["result_text"] + " " + text)
                continue
            if cur["_mode"] == "after_result" and (text[:1].islower() or text[:1] in "(,;") and cur["tests"]:
                # tail of a cell that wrapped onto the next page / below the result: belongs to the previous test
                cur["tests"][-1]["procedure"] = _clean(cur["tests"][-1]["procedure"] + " " + text)
                continue
            if TEST_START.match(text) and text[:1].isupper():   # procedures start a new sentence
                if cur["_proc"]:
                    close_test()
                cur["_mode"] = "test"
                cur["_proc"].append(text)
                continue
            if cur["_mode"] == "desc":
                first, *rest = INLINE_TEST.split(text, maxsplit=1)
                cur["_desc"].append(first)
                if rest:
                    cur["_mode"] = "test"
                    cur["_proc"].append(rest[0].strip())
            elif cur["_mode"] == "test":
                cur["_proc"].append(text)
            else:   # text after a result that isn't a recognised test start: likely an unusual procedure verb
                cur["_mode"] = "test"
                cur["_proc"].append(text)
    finish()

    out = []
    for c in controls.values():
        desc = _clean(" ".join(c["_desc"]))
        if not c["criteria"]:
            issues.append({"id": c["id"], "page": c["_page"], "problem": "no criterion heading above this control"})
        if not c["tests"]:
            issues.append({"id": c["id"], "page": c["_page"], "problem": "no test procedures parsed"})
        for t in c["tests"]:
            if t["result"] is None:
                t["result"] = "other"
                issues.append({"id": c["id"], "page": c["_page"], "problem": "test procedure with no recognised result"})
        out.append({
            "id": c["id"],
            "criteria": c["criteria"],
            "description": desc,
            "tests": c["tests"],
            "evidence": {"quote": _quote(_clean(" ".join(c["_desc"][:3])) if len(desc.split()) >= 10
                                         else desc + " " + (c["tests"][0]["procedure"] if c["tests"] else "")),
                         "pdf_page": c["_page"], "printed_page": doc.printed_page(c["_page"])},
            "_pages": c["_pages"],
        })
    results = {
        "controls": len(out),
        "tests": sum(len(c["tests"]) for c in out),
        "clean": sum(1 for c in out for t in c["tests"] if t["result"] == "no_exceptions"),
        "exceptions": sum(1 for c in out for t in c["tests"] if t["result"] == "exception"),
    }
    return {"controls": out, "issues": issues, "criteria_pages": criteria_pages, "results": results}


# ======================================================================================================
# Column-aware parser (pypdf "layout" text). Handles tables where controls are numbered ("42. The
# organization ...") with a criteria-reference column, and tables with no control IDs at all, where
# controls sit under criterion headings ("CC2.3 The entity ..."). Columns are found from where text
# actually starts on the page, not from header labels.
# ======================================================================================================
from collections import Counter

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*?\.(?:com|net|org|io|ly|co|edu|gov)")
SEG = re.compile(r"\S+(?: \S+)*")                      # runs of text separated by 2+ spaces
CRIT_LIST = re.compile(r"^(?:(?:CC|PI|A|C|P)\d{1,2}\.\d{1,2},?\s*)+$")
CRIT_ID = re.compile(r"(?:CC|PI|A|C|P)\d{1,2}\.\d{1,2}")
NUM_ID = re.compile(r"^((?:[A-Z]{1,3}[-.]?)?\d{1,3}(?:\.\d{1,3}){0,2})\.?\s+(\S.*)$")
AUDITOR_NOTE = re.compile(r"^(A portion of the control|The control did not|This control did not|Note:)", re.I)
RESULT_START = re.compile(r"^(no (?:relevant )?(?:exceptions?|deviations?)|not tested|exceptions? noted|deviations? noted|"
                          r"the following|[A-Z])", re.I)


def _layout_lines(path, pages, password=None):
    from pypdf import PdfReader
    r = PdfReader(path)
    if r.is_encrypted:
        r.decrypt(password or "")
    out = {}
    for p in pages:
        t = r.pages[p - 1].extract_text(extraction_mode="layout") or ""
        lines = []
        for ln in t.splitlines():
            ln = EMAIL.sub(lambda m: " " * len(m.group()), ln)       # watermark: blank it, keep positions
            lines.append(ln.rstrip())
        out[p] = lines
    # Running headers/footers: a line (digits ignored) near the top or bottom of the page that repeats there on
    # a third or more of the pages. Only the edges count: table rows can repeat too, and must never be dropped.
    keyf = lambda ln: re.sub(r"\s+", " ", re.sub(r"\d+", "#", ln.strip()))  # noqa: E731

    def edges(lines, n=4):
        idx = [i for i, ln in enumerate(lines) if len(ln.strip()) >= 6]
        return set(idx[:n] + idx[-n:])
    edge_idx = {p: edges(lines) for p, lines in out.items()}
    # a table row has several columns of text; a header or footer has one or two runs
    edge_idx = {p: {i for i in ix if len(SEG.findall(out[p][i])) <= 2} for p, ix in edge_idx.items()}
    seen = Counter(k for p, lines in out.items() for k in {keyf(lines[i]) for i in edge_idx[p]})
    furniture = {k for k, n in seen.items() if n >= max(3, len(out) // 3)}
    for p in out:
        out[p] = ["" if (i in edge_idx[p] and keyf(ln) in furniture) else ln for i, ln in enumerate(out[p])]
    return out


def _columns(page_lines):
    """Left edges of the table's columns, from a histogram of segment start positions."""
    starts = Counter()
    for lines in page_lines.values():
        for ln in lines:
            for m in SEG.finditer(ln):
                starts[m.start()] += 1
    total = sum(starts.values()) or 1
    # positions jitter by a few characters from page to page: cluster within +/-4 before picking peaks
    agg = {pos: sum(starts.get(q, 0) for q in range(pos - 4, pos + 5)) for pos in starts}
    peaks = []
    for pos, n in sorted(agg.items(), key=lambda x: -x[1]):
        if n < total * 0.025:
            break
        if all(abs(pos - q) > 9 for q in peaks):
            peaks.append(pos)
    # use each cluster's left edge, so text drawn slightly left still lands in the right column
    edges = []
    for pk in peaks:
        near = [q for q in range(pk - 4, pk + 5) if starts.get(q, 0) >= 0.1 * starts.get(pk, 1)]
        edges.append(min(near) if near else pk)
    return sorted(edges)


def _split(ln, cols):
    """Assign each text run in a line to the column whose left edge is nearest at or before it."""
    out = {}
    for m in SEG.finditer(ln):
        s = m.start()
        c = min(range(len(cols)), key=lambda i: abs(cols[i] - s))   # nearest edge: pages shift by several characters
        out[c] = (out[c] + " " + m.group()) if c in out else m.group()
    return out


def _next_id(prev, cand):
    """Numbered controls run in order. Accept a candidate ID only if it plausibly follows the previous one, so a
    wrapped line like "7 worldwide." (from "24 x 7 worldwide") isn't taken for control 7."""
    if prev is None:
        return True
    a, b = re.findall(r"\d+", prev), re.findall(r"\d+", cand)
    if not a or not b or re.sub(r"[\d.]", "", prev) != re.sub(r"[\d.]", "", cand):
        return re.sub(r"[\d.]", "", prev) != re.sub(r"[\d.]", "", cand)   # new prefix (e.g. A1 after CC) is fine
    a, b = [int(x) for x in a], [int(x) for x in b]
    if len(a) == 1 and len(b) == 1:
        return 0 < b[0] - a[0] <= 3
    return b > a


def parse_layout(doc, pages):
    pl = _layout_lines(doc.path, pages)
    cols = _columns(pl)
    if len(cols) < 3:
        return None
    # identify column roles from content
    votes = {i: Counter() for i in range(len(cols))}
    for lines in pl.values():
        for ln in lines:
            for c, txt in _split(ln, cols).items():
                if RESULT_OK.search(txt) and len(txt.split()) <= 8:
                    votes[c]["result"] += 1
                if TEST_START.match(txt) and txt[:1].isupper():
                    votes[c]["test"] += 1
                if CRIT_LIST.match(txt.strip()) or CRIT_ID.fullmatch(txt.strip().rstrip(",")):
                    votes[c]["crit"] += 1
                if NUM_ID.match(txt) and not CRIT_ID.match(txt):
                    votes[c]["id"] += 1
                votes[c]["text"] += len(txt)
    res_c = max(votes, key=lambda c: votes[c]["result"])
    test_c = max((c for c in votes if c != res_c), key=lambda c: votes[c]["test"])
    left = [c for c in votes if c < test_c]
    if not left:
        return None
    crit_c = max(left, key=lambda c: votes[c]["crit"]) if any(votes[c]["crit"] for c in left) else None
    desc_c = max((c for c in left if c != crit_c), key=lambda c: votes[c]["text"], default=None)
    if desc_c is None:
        return None
    numbered = votes[desc_c]["id"] >= 5
    layout = "numbered controls + criteria column" if numbered else "criterion headings, unnumbered controls"

    controls, issues, criteria_pages = [], [], {}
    cur, heading, skip_heading = None, None, False
    counter = Counter()

    def new_control(cid, first_desc, p, synthetic):
        nonlocal cur
        cur = {"id": cid, "criteria": [heading] if (heading and not numbered) else [], "_desc": [first_desc],
               "tests": [], "_page": p, "_pages": [p], "_synthetic": synthetic}
        controls.append(cur)

    for p in pages:
        prev_blank, prev_desc = True, False
        for ln in pl[p]:
            if not ln.strip():
                prev_blank, prev_desc, skip_heading = True, False, False
                continue
            seg = _split(ln, cols)
            d, t, r = seg.get(desc_c, ""), seg.get(test_c, ""), seg.get(res_c, "")
            cr = seg.get(crit_c, "") if crit_c is not None else ""
            # page furniture: header rows, running titles
            if re.search(r"tests? performed|service auditor|results of tests|^\s*results\s*$|control description|"
                         r"trust services criteria and|^\s*reference\b|confiden[tc]ial information", ln, re.I) and not r[:2].lower() == "no":
                # a repeated table header: treat it like the blank line it replaces
                prev_blank, prev_desc, skip_heading = True, False, False
                continue
            # criterion heading row (unnumbered layout): ID in the criteria column + criterion text
            if not numbered and cr and CRIT_ID.fullmatch(cr.strip()) and (d or t):   # long headings spill into other columns
                heading = cr.strip()
                criteria_pages.setdefault(heading, [])
                skip_heading = True        # heading text may wrap onto following lines until a blank line
                prev_blank = False
                continue
            if skip_heading:
                continue
            # control start
            if numbered:
                m = NUM_ID.match(d) if d else None
                if m and not CRIT_ID.match(d) and _next_id(cur["id"] if cur else None, m.group(1)):
                    new_control(m.group(1), m.group(2), p, False)
                    d = ""
            elif d and (prev_blank or not prev_desc) and not (cur and p == cur["_page"] and d[:1].islower()) \
                    and not (cur and not prev_desc and p != cur["_pages"][-1] and d[:1].islower()):
                if d[:1].isupper() and not AUDITOR_NOTE.match(d):   # an auditor's note belongs to the control above
                    counter[heading] += 1
                    new_control(f"{heading or 'X'}#{counter[heading]}", d, p, True)
                    d = ""
            if cur is None:
                prev_blank, prev_desc = False, bool(seg.get(desc_c))
                continue
            if p not in cur["_pages"]:
                cur["_pages"].append(p)
            if heading and not numbered:
                criteria_pages.setdefault(heading, [])
                if p not in criteria_pages[heading]:
                    criteria_pages[heading].append(p)
            if d:
                cur["_desc"].append(d)
            if cr and numbered:
                for c in CRIT_ID.findall(cr):
                    if c not in cur["criteria"]:
                        cur["criteria"].append(c)
                        criteria_pages.setdefault(c, [])
                    if p not in criteria_pages[c]:
                        criteria_pages[c].append(p)
            # tests: a new test starts on the line where a new result starts
            last = cur["tests"][-1] if cur["tests"] else None
            if r and r[:1].islower() and (last is None or last["_closed"]):
                t, r = (t + " " + r).strip(), ""          # a long procedure line spilling into the results column
            # (a deviation's explanation also sits in the results column, but next to a continuing procedure
            #  or blank test cell, so it extends the previous result instead of starting a new test)
            if r and RESULT_START.match(r) and (last is None or (last["_closed"] and t[:1].isupper())):
                cur["tests"].append({"procedure": t, "result_text": r, "_closed": r.rstrip().endswith("."), "_page": p})
            else:
                if last is None and t:
                    cur["tests"].append({"procedure": t, "result_text": "", "_closed": False, "_page": p})
                elif last is not None:
                    if t:
                        last["procedure"] += " " + t
                    if r:
                        last["result_text"] += " " + r
                        last["_closed"] = r.rstrip().endswith(".")
            prev_blank, prev_desc = False, bool(seg.get(desc_c))

    out = []
    for c in controls:
        if c["_synthetic"] and c["id"].startswith("X#") and not c["tests"]:
            continue        # text above the first criterion heading (an intro paragraph), not a control
        desc = _clean(" ".join(c["_desc"]))
        tests = []
        for t in c["tests"]:
            rt = _clean(t["result_text"])
            if RESULT_OK.search(rt) and len(rt.split()) <= 8:
                res = "no_exceptions"
            elif RESULT_NT.search(rt) or re.search(r"\bnot tested\b|did not (?:operate|occur)", rt, re.I):
                res = "not_tested"
            elif RESULT_BAD.search(rt) or re.search(r"\bexceptions?\b|\bdeviations?\b", rt, re.I):
                res = "exception"
            else:
                res = "other"
            if res != "no_exceptions" and not any(i["id"] == c["id"] and i.get("kind") == res for i in issues):
                issues.append({"id": c["id"], "page": t["_page"], "problem": f"non-standard result ({res}): AI to read the wording",
                               "kind": res})
            tests.append({"procedure": _clean(t["procedure"]), "result": res, "result_text": rt})
        if not tests:
            issues.append({"id": c["id"], "page": c["_page"], "problem": "no test procedures parsed"})
        if not c["criteria"]:
            issues.append({"id": c["id"], "page": c["_page"], "problem": "no criterion reference for this control"})
        words = desc.split()
        quote = " ".join(words[:24]) if len(words) >= 6 else (desc + " " + (tests[0]["procedure"] if tests else "")).strip()
        rec = {"id": c["id"], "criteria": c["criteria"], "description": desc, "tests": tests,
               "evidence": {"quote": " ".join(quote.split()[:24]), "pdf_page": c["_page"],
                            "printed_page": doc.printed_page(c["_page"])},
               "_pages": c["_pages"]}
        if c["_synthetic"]:
            rec["id_source"] = "synthetic"
        out.append(rec)
    results = {"controls": len(out), "tests": sum(len(c["tests"]) for c in out),
               "clean": sum(1 for c in out for t in c["tests"] if t["result"] == "no_exceptions"),
               "exceptions": sum(1 for c in out for t in c["tests"] if t["result"] == "exception"),
               "layout": layout}
    return {"controls": out, "issues": issues, "criteria_pages": criteria_pages, "results": results}


RESULT_LINE = re.compile(r"(?mi)^\s*no (?:relevant )?(?:exceptions?|deviations?) (?:were |was )?(?:noted|identified|found)\.?\s*$")


def _best_quote(doc, page, text, max_words=24, min_words=5):
    """Longest opening run of `text` (up to max_words) that appears verbatim on the page, so every quote the
    parser emits passes the verifier's check. The raw text layer interleaves table columns line by line, so a
    description is often contiguous for only its first few lines."""
    from soc2v2.pdftext import normalize
    words = text.split()
    for pg in (page, page + 1, page - 1):
        if not 1 <= pg <= doc.n_pages:
            continue
        hay = doc.norm[pg - 1]
        squashed = hay.replace(" ", "")
        for n in range(min(max_words, len(words)), min_words - 1, -1):
            q = normalize(" ".join(words[:n]))
            if q in hay or q.replace(" ", "") in squashed:
                return " ".join(words[:n]), pg
    return None, page


def parse_best(doc, pages):
    """Run both parsers; keep the one whose clean-result count best matches an independent count of
    stand-alone result lines in the PDF text (ties: fewer issues)."""
    target = sum(len(RESULT_LINE.findall(doc.raw[p - 1])) for p in pages)
    cands = [parse(doc, pages)]
    cands[0]["results"].setdefault("layout", "criterion headings + numbered controls (line parser)")
    try:
        lay = parse_layout(doc, pages)
        if lay:
            cands.append(lay)
    except Exception as e:  # noqa: BLE001  (a parser failure must never stop the extraction)
        cands[0]["issues"].append({"id": "-", "page": pages[0], "problem": f"layout parser failed: {e}"})
    best = min(cands, key=lambda r: (abs(r["results"]["clean"] - target), len(r["issues"])))
    best["results"]["independent_clean_count"] = target
    best["results"]["parser_version"] = PARSER_VERSION
    diff = abs(best["results"]["clean"] - target) / max(target, 1)
    best["results"]["layout_gate"] = {
        "status": "PASS" if diff <= GATE_TOLERANCE and best["results"]["controls"] > 0 else "UNVALIDATED",
        "detail": f"parser {best['results']['clean']} clean vs independent {target} ({diff:.1%}; tolerance {GATE_TOLERANCE:.0%})"}
    for c in best["controls"]:
        ev = c["evidence"]
        src = c["description"] if len(c["description"].split()) >= 5 else \
            (c["description"] + " " + (c["tests"][0]["procedure"] if c["tests"] else "")).strip()
        q, pg = _best_quote(doc, ev["pdf_page"], src)
        for t in ([] if q else c["tests"]):          # description too fragmented: quote a test procedure instead
            q, pg = _best_quote(doc, ev["pdf_page"], t["procedure"])
            if q:
                break
        if not q:
            q, pg = " ".join(src.split()[:24]), ev["pdf_page"]
            best["issues"].append({"id": c["id"], "page": pg, "problem": "no verbatim quote located for this control"})
        ev["quote"] = q
        if pg != ev["pdf_page"]:
            ev["pdf_page"], ev["printed_page"] = pg, doc.printed_page(pg)
    return best
