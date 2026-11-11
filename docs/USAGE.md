# Usage

## Setup

```bash
pip install -r requirements.txt        # pypdf, openpyxl, pyyaml
# optional: Ollama for local extraction (https://ollama.com), then e.g.  ollama pull gemma4:26b
# optional: pip install reportlab      # only to regenerate the sample reports
```

## Folder layout

```
Vendors/
└── <Vendor>/                            # one folder per vendor (git-ignored except the samples)
    ├── <vendor> SOC 2 report.pdf
    ├── <vendor> Bridge Letter.pdf       # optional; "Bridge" in the name
    ├── scope_profile.yaml               # how you use this vendor
    ├── extraction*.json                 # one per extraction method / model
    ├── dispositions.yaml                # your decisions on review items (optional)
    └── Review_<date>/v2/                # verifier reports (.md + .json)
```

`--vendor` always takes the folder name under `Vendors/`.

## Steps

1. **Vendor folder.** Create `Vendors/<Vendor>/` and add the report (and bridge letter, if any). The bridge letter is read automatically if it mentions the report's period end. The gap check then runs from the date it covers through.
2. **Scope profile.** Copy `soc2v2/profiles/_example_profile.yaml` to `Vendors/<Vendor>/scope_profile.yaml` and edit it: services used, data types, required criteria categories, age and bridge-letter thresholds. Or fill in `soc2v2/profiles/CyberCede_Vendor_Use_Profile_TEMPLATE.xlsx` (its Rules tab drives the tier) and run `python3 profile_from_xlsx.py Vendors/<Vendor>/<workbook>.xlsx`.
3. **Extraction.** Pick one:
   - **Hybrid, local (default):** `python3 extract_hybrid.py --vendor <Vendor> --model gemma4:26b`. Code parses the testing tables; the model answers only targeted questions; every answer is checked and re-asked once if wrong; anything still wrong is listed under `extractor.unresolved` and becomes a review item. Add `--criteria all` to also ask about every criterion as a cross-check against the parser. `--dry-run` runs the parser alone.
   - **Full transcription, local:** `python3 extract_local.py --vendor <Vendor> --model gemma4:26b`. Slower, and it skips more. Use it when the layout gate says the parser can't read this report.
   - **Cloud model:** `python3 soc2_review.py prompt`, then give the prompt, the PDF and `soc2v2/schema/extraction.schema.json` to an approved model, and save its JSON as `Vendors/<Vendor>/extraction.json`.
4. **Verify.** `python3 soc2_review.py verify --vendor <Vendor> [--extraction Vendors/<Vendor>/<file>.json] [--date YYYY-MM-DD] [--overlay ccm] [--password …]`
5. **Decide.** Read REVIEW and FAIL items in the report. `--dispositions-template` writes a `dispositions.yaml` stub to record accept / mitigate / follow-up decisions with your reasons; re-running verify applies them.
6. **Outputs.** Write the client's questionnaire, ticket or memo only from verified data.

## Comparing methods and models

- `bash run_comparison.sh <Vendor>`: both hybrid modes on one vendor, verified, with a comparison table.
- `bash run_batch.sh "<Vendor A>" "<Vendor B>"`: full, hybrid and hybrid-all on each vendor overnight. It keeps a Mac awake and logs to `batch_<date>.log`.
- `python3 compare_methods.py --vendor <Vendor>`: time, tokens, controls found, quote accuracy, verifier results. If `Vendors/<Vendor>/extraction_baseline_claude.json` (or any reviewed baseline you name that way) exists, it also scores each method against it: deviations found or missed, report facts right or wrong.

## Changing the parser

Follow [PARSER_METHOD.md](PARSER_METHOD.md): bump `PARSER_VERSION`, add a CHANGELOG entry, and run `python3 tools/check_samples.py` and `python3 run_regression.py` before using the change. Keep your own signed-off reports as **local** regression cases (add them to `tests/regression/cases.json` in your working copy). Never commit them.

## Rules of the road

- AI reads; code checks; a human decides. Nothing here approves a vendor.
- Every fact needs a verbatim quote and a page. Citations say which page number they use (PDF page vs. printed page).
- Use only AI services your vendor NDAs and data-handling terms allow; the local options exist for when none are allowed.
