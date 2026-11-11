# soc2-verify

**Verify AI-assisted SOC 2 Type II reviews: code reads, AI answers, code checks, a human decides.**

[![tests](https://github.com/metajunkie/soc2-verify/actions/workflows/tests.yml/badge.svg)](https://github.com/metajunkie/soc2-verify/actions/workflows/tests.yml)

An AI can read a 200-page SOC 2 report in minutes and produce a confident, polished summary. That summary can still be wrong in the details: a control it skipped, a deviation it missed, an encryption claim the report never makes. This project keeps the speed and makes every claim checkable. Every fact must come with a verbatim quote and a page number, and deterministic code (not another AI) checks each one against the PDF.

It came out of a talk, *Trust, but Verify: What happened when I audited my own AI-assisted SOC 2 review* (ISACA chapter meeting, November 12, 2026). The short version: the first version produced a verification log that verified nothing, so I rebuilt it three times.

> **Status: working, measured, not validated.** It has been run on four real reports from three audit firms, and on the fictional samples included here. New table layouts have so far needed code changes. Read [Status and limits](#status-and-limits) before relying on it.

---

## How it works

| Step | Who | What |
|---|---|---|
| 1. Scope profile | Human | How you use this vendor: services, data, which Trust Services Criteria categories you need |
| 2. Parse the tables | Code | Reads Section IV directly: control IDs, criteria, test procedures, results. Quotes are copied from the page |
| 3. Ask the AI | Any model | Only what code can't settle: the opinion, dates, carve-outs, user-entity controls, any non-clean result, any criterion code couldn't match. Every answer = page + verbatim quote |
| 4. Check answers | Code | Wrong answers go back to the model once with the error spelled out; still wrong goes to the reviewer |
| 5. Risk decision | Human | Never automated |

The verifier (`soc2v2/verifier.py`) runs on any extraction, however it was produced. It checks:

- **Quotes**: each one is on the page it cites.
- **Opinion**: no qualification, adverse or disclaimer wording in the opinion section. Auditor scope exclusions are flagged separately.
- **Exceptions**: an independent scan of the whole PDF for exception wording, compared with what the extraction reports.
- **Completeness**: control IDs and clean test results reconcile to the PDF.
- **Criteria**: every required Trust Services Criterion has a tested control.
- **Scope**: the services you use appear in the system description; required categories are in scope.
- **Dates**: report age, plus bridge letters (read automatically) and the uncovered gap.
- **Carve-outs**: subservice organizations whose controls weren't tested.

Each check is PASS, REVIEW (a human needs to look) or FAIL (contradicted by the source), with the evidence attached.

## Quickstart

```bash
git clone https://github.com/metajunkie/soc2-verify.git && cd soc2-verify
pip install -r requirements.txt

python3 tools/check_samples.py        # parser vs. the samples' known answers
python3 run_regression.py             # parser + verifier vs. locked snapshots

# Verify the fictional sample's ground-truth extraction
python3 soc2_review.py verify --vendor Sample-A --extraction Vendors/Sample-A/extraction_truth.json --date 2026-11-10
```

The report lands in `Vendors/Sample-A/Review_<date>/v2/`. Sample A shows typical review items: a gap with no bridge letter, a product the report never mentions, and a carved-out hosting provider. Sample B passes, because its bridge letter covers the gap. Sample C fails: its report is over 12 months old.

### Extract with a local model (nothing leaves your machine)

Install [Ollama](https://ollama.com) and pull a model (tested with `gemma4:26b`), then:

```bash
python3 extract_hybrid.py --vendor Sample-A --model gemma4:26b       # code parser + targeted questions
python3 soc2_review.py verify --vendor Sample-A --extraction Vendors/Sample-A/extraction_hybrid_gaps_gemma4_26b.json
```

`extract_local.py` is the older method, where the model transcribes the whole report. It's kept for layouts the parser can't read, and for comparison (`compare_methods.py`, `run_batch.sh`).

### Use a cloud model instead

`python3 soc2_review.py prompt` prints the extraction prompt. Give it, the report and `soc2v2/schema/extraction.schema.json` to any approved model, save the JSON as `Vendors/<Vendor>/extraction.json`, and run `verify`. Use only models your vendor NDAs and data-handling terms allow.

## Your own reports

1. Make `Vendors/<Vendor>/` and put the SOC 2 report in it. Put any bridge letter there too, with "Bridge" in its file name. **`Vendors/` is git-ignored except for the samples. Never commit a real report or anything derived from one.**
2. Write a scope profile: copy `soc2v2/profiles/_example_profile.yaml` to `Vendors/<Vendor>/scope_profile.yaml`, or fill in the workbook template and run `profile_from_xlsx.py`.
3. Extract (local or cloud, above), then `verify`.
4. Read the REVIEW and FAIL items. Record your decisions with `--dispositions-template`.

Criteria baseline: the repo ships criterion IDs with short paraphrased labels (`soc2v2/criteria/tsc_2017.json`). For the full AICPA text and points of focus, download the Trust Services Criteria from AICPA and run `soc2v2/criteria/build_tsc_from_aicpa.py`. For a CSA Cloud Controls Matrix view, download CCM from CSA under its license and run `build_ccm_crosswalk.py`. Neither organization's content is included here.

Full walkthrough: [docs/USAGE.md](docs/USAGE.md).

## Status and limits

- **It isn't a validated tool.** It has been run on four real reports (three audit firms, three table layouts) and three fictional samples. On the real reports, the code-plus-questions method found every control with a deviation that the reviewer baseline recorded (13 of 13), in about a tenth of the time and tokens of having the model transcribe the report. That's a small sample.
- **New report layouts have needed code changes.** Every auditor formats Section IV differently. [docs/PARSER_METHOD.md](docs/PARSER_METHOD.md) explains how the parser works and the procedure for a new layout: an automatic gate, a human sample sign-off, and a regression case. If the parser's counts don't match an independent count, the report is flagged "unvalidated layout" instead of being trusted.
- **The parser and the verifier share PDF-reading code,** so they can share a blind spot. The independent count, the AI cross-check (`--criteria all`) and the regression set are the counterweights. None replaces a human sample.
- **Scanned PDFs** (no text layer) aren't supported; run OCR first.
- **It doesn't make the risk decision.** It tells you what the report does and doesn't support. You sign.

Nothing here is legal or audit advice.

## Repository layout

```
soc2_review.py              CLI: verify, prompt
extract_hybrid.py           code parser + targeted model questions (default)
extract_local.py            full transcription by a local model
compare_methods.py          time / tokens / verifier results per method (+ vs. a baseline)
run_regression.py           parser + verifier regression against locked snapshots
run_batch.sh                overnight runs across vendors
soc2v2/                     parser, verifier, PDF text, schema, prompt, criteria, profile template
tools/                      sample generator and ground-truth check
tests/regression/           regression cases and snapshots
integrations/open-webui/    optional local service + Open WebUI tool for a chat assistant
Vendors/Sample-A|B|C/       fictional reports, one per supported layout
```

## How this was built

This was built by a practitioner working with an AI coding assistant (Claude). The AI wrote much of the code; the design decisions, and the decision to audit the AI's own output, were the author's. That's the point of the project: the AI's work is checked by deterministic code, and that code is checked by regression tests and human samples.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
