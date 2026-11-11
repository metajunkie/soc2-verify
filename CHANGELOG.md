# Changelog

Changes to the parser, extractors and verifier. Before using any parser change on a real review, run
`python3 tools/check_samples.py` and `python3 run_regression.py` (see docs/PARSER_METHOD.md).

## 2026-11-11: first public release (parser 2026.11.11-2, verifier 2.0.0)

- Fictional sample reports (`Vendors/Sample-A|B|C`), one per supported layout, with known ground truth
  (`tools/make_samples.py`, `tools/check_samples.py`). Regression cases and a CI workflow run on them.
- Parser: running headers/footers are removed only at the top or bottom of a page, and only if they're short;
  table rows that repeat are never dropped. Control IDs may stand on their own line. Multi-line exception and
  "not tested" results stay together. A table header repeated on a later page is ignored, including when it
  is split one column per line. Intro text above the first criterion heading isn't read as a control.
  (One of these changes altered how a real report was read; the regression set caught it before use, and it
  was fixed.)
- `PARSER_VERSION`, recorded in every hybrid extraction.
- Layout gate: if the parser's clean-result count differs from an independent count by more than 2%, the
  report is treated as an unvalidated layout (warning, unresolved item, verifier REVIEW).
- Verifier reports everything the extractor left unresolved.
- Hybrid "all criteria" mode no longer accepts placeholder control IDs from the model.
- `run_regression.py` and `docs/PARSER_METHOD.md`.

## 2026-11-10: three table layouts

- Column-aware parser for numbered controls with a criteria column, and for unnumbered controls under
  criterion headings. Chooses between readers by matching an independent count of result lines.
- Fixed: testing pages holding only a deviation were skipped. The verifier's independent exception scan
  caught it.
- Testing section found by stand-alone result lines, not by "Tests Performed" (which appears on covers).
- Report PDF chosen explicitly; bridge letters are read, and the coverage gap runs from the date they cover through.
- Day-first dates ("01 August 2025") recognized.
- Exception scanner: "except for" only counts in the opinion section; "no opinion on X" is reported as an
  auditor scope exclusion; methodology text and glued words no longer raise false alarms.
- "User Entity Responsibilities" searched alongside complementary user entity controls.
- Control IDs may be bare numbers ("42.") or absent (reconciled by test results).

## 2026-11-10: hybrid extraction ("attempt 3")

- `soc2v2/parse_controls.py` and `extract_hybrid.py`: code parses the testing tables; the model answers
  targeted questions with page + quote, checked by code and re-asked once.
- `compare_methods.py`, `run_comparison.sh`, `run_batch.sh`.
- Verifier: empty quotes reported as omissions, not "possible fabrication"; criterion headings listed as
  controls reported separately; clean results reconciled clean-to-clean.

## Earlier

- Deterministic verifier, extraction schema and prompt, scope profiles, Trust Services Criteria baseline,
  local-model extraction (`extract_local.py`).
