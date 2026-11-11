# How the control parser works, and how a new report layout gets validated

Status, 2026-11-11: the parser reads three table layouts. Each was built while reviewing a real report and checked against that PDF; each also has a fictional sample in `Vendors/Sample-*` whose correct answers are known. The real reports aren't in this repository (they're under NDA). Until a layout is signed off on *your* reports (step 3 below), treat parsed controls from it as a strong first draft that a human checks, not as validated output.

---

## 1. What the parser does

In a SOC 2 Type II report, Section IV is a table: each control, the criteria it supports, the auditor's test procedures and the result of each test. The parser (`soc2v2/parse_controls.py`) reads that table with code, not AI, so the controls, tests and results are copied from the page and can't be invented. The AI is only asked about what the parser can't settle (`extract_hybrid.py`).

It finds the testing section first (`verifier._control_pages`). It looks for pages with a stand-alone result line ("No exceptions noted." / "No deviations noted."), takes the largest cluster of them, and fills the gaps between them, because a page holding only a deviation has no clean result line but is the page that matters most.

### Two readers

**Line reader** (`parse`). Reads the plain text top to bottom. It works when each control starts with its own ID under a criterion heading, e.g. `CC1.2 - …` then `1.2.1 The Company has …` (the description may start on the ID's line or the next one).

**Column reader** (`parse_layout`). Uses pypdf's "layout" text mode, which keeps text where it sits on the page so the table's columns stay apart.

1. **Find the column edges.** On every testing page, note where each run of text begins. The common starting positions are the column edges. They drift a few characters between pages, so positions within ±4 characters are grouped, and each group's leftmost position is the edge.
2. **Decide what each column is, from its content.** The column where "No deviations noted." keeps appearing is Results. The one whose lines start "Inspected", "Inquired", "For a sample" is Tests. The one full of criteria IDs ("CC6.1, CC6.2") is Criteria. The widest remaining one to the left is the control Description.
3. **Assign text to columns.** Each run of text goes to the nearest column edge.
4. **Remove page furniture.** Any line that repeats, digits ignored, on a third or more of the testing pages (running headers, footers, "page 12 / 121") is dropped. Email watermarks are blanked out without shifting positions.
5. **Split into controls and tests.**
   - *Numbered layout* ("42. The organization …"): a new control starts when the description column begins with the next number in sequence (within 3). That rule stops a wrapped line like "7 worldwide." (from "24 x 7 worldwide") from being read as control 7.
   - *Unnumbered layout* (controls listed under criterion headings, no IDs): a criterion heading is a lone criterion ID with text beside it. A new control starts when the description column starts again after a gap. The auditor's note "A portion of the control did not operate …" stays with the control above it. These controls get IDs like `CC9.2#3` (criterion + position), marked `id_source: synthetic`.
   - *Tests*: a new test starts on the line where a new result starts beside a new procedure (capitalized). A deviation's write-up also sits in the Results column, but beside a continuing procedure or an empty Tests cell, so it extends the previous result instead of creating a fake test. A lowercase fragment in the Results column after a finished result is a long procedure line spilling over, and goes back to the procedure.
6. **Classify each result.** "No exceptions/deviations noted" means clean. "Not tested", "no instances", "did not occur" or "did not operate" means not tested. Deviation or exception wording means exception. Anything else is "other". Every non-clean result is handed to the AI, which must quote the actual wording.

### Choosing a reading, and quotes

`parse_best` runs both readers and keeps the one whose clean-result count is closest to an **independent count**: stand-alone "No … noted" lines counted directly in the PDF text. Each control's evidence quote is the longest opening stretch of its description that appears word for word on the page (the plain text layer interleaves columns, so a description is often continuous for only its first lines). If no stretch of 5+ words is found, it quotes the first test procedure instead. If neither works, it's reported as an issue rather than invented.

### What it was checked against

Real reports (not included; vendors anonymized):

| Report | Layout | Pages | Controls | Tests | Clean (independent count) |
|---|---|---|---|---|---|
| Vendor 1 | Criterion headings + numbered controls (1.2.3) | 54 | 135 | 165 | 164 (164) |
| Vendor 2 | Numbered controls (42.) + criteria column | 236 | 145 | 638 | 627 (625) |
| Vendor 3 | Numbered controls (42.) + criteria column | 191 | 147 | 643 | 633 (636) |
| Vendor 4 | Criterion headings, unnumbered controls | 121 | 203 | 364 | 350 (356) |

Every evidence quote in all four verifies against its page. Every deviation in the reviewer baseline was flagged by the parser, plus one false flag (a result line read badly), which the AI step corrected when it read the actual wording.

Fictional samples (included): `tools/check_samples.py` checks all three exactly (40 of 40 controls each, every result and quote).

### Known limits

- Each new layout so far needed code changes. Expect the next new audit firm to need some too.
- The parser and the verifier share PDF-reading code, so they can share a blind spot. The independent count, the AI cross-check (`--criteria all`) and the regression set are the counterweights. None of them replaces a human sample on a new layout.
- The independent count only sees the pages the testing-section finder returns. If that finder is wrong, the parser and the count can agree and both be wrong. The regression set caught exactly this when an earlier page-range bug was deliberately reintroduced; the layout gate did not. It also caught a parser change on 2026-11-11 that fixed the samples but altered how a real report was read, before that change was used.
- Scanned PDFs (no text layer) aren't supported; OCR would come first.

---

## 2. Procedure for a new report layout

A layout counts as **supported** only after all four steps. Until then, reviews that use it say so.

### Step 1: automatic gate (every run)

`parse_best` compares its clean-result count with the independent count. If they differ by more than 2% (`GATE_TOLERANCE`), or no controls were parsed, the layout gate is **UNVALIDATED**. Then:

- `extract_hybrid.py` prints a warning and lists the gate as an unresolved item.
- The verifier shows "Parser layout gate" as REVIEW in the report, and every unresolved item reaches the reviewer.
- Use `extract_local.py` (full AI transcription) for that report, or review the parsed controls by hand.

Passing the gate is necessary, not sufficient (see Known limits).

### Step 2: change the parser, if needed

- Make the change. Bump `PARSER_VERSION` in `soc2v2/parse_controls.py`, and add a line to `CHANGELOG.md` saying what changed and why.
- Run `python3 run_regression.py`. **Every existing case must still pass.** If a change alters an existing case on purpose, re-lock it with `--update --reason "…"`, which is logged in `tests/regression/UPDATES.md`, and re-do that case's human sample (step 3).

### Step 3: human sign-off (once per layout)

A reviewer re-performs a sample against the PDF and records the result:

- **10 controls** chosen across the testing section (start, middle, end, and at least one that spans a page break). Check the ID, criteria, number of tests and each result.
- **Every non-clean result** (exceptions and not tested). Check that the wording and the control match.
- **The counts.** Parser controls and clean results against the independent count, and against the report's own totals if it states them.

Then fill in `signed_off_by`, `signed_off_on` and a short `sign_off_note` for the case in `tests/regression/cases.json`. Disagreements are fixed (step 2) before sign-off, not noted around.

### Step 4: lock it

Add the report as a case in `tests/regression/cases.json` (pin the review date and the extraction file) and run `python3 run_regression.py` to create its snapshot. From now on, any change that alters how this report is read fails the regression run until someone looks.

---

## 3. Versions and records

- `PARSER_VERSION` is recorded in every hybrid extraction (`extractor.stats.parser_version`), so a review can always say which parser read it.
- `CHANGELOG.md`: what changed, when, and why.
- `tests/regression/`: the cases, their snapshots, and the log of every deliberate snapshot update.
- `.gitignore` keeps everything under `Vendors/` out of git except the samples. Keep it that way.
