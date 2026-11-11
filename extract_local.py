#!/usr/bin/env python3
"""
Local-model SOC 2 extraction (nothing leaves this machine).

Runs the same extraction prompt and JSON schema as the cloud workflow, but against a model
served locally by Ollama (or any Ollama-compatible server). Output is a normal extraction.json
that the deterministic verifier checks exactly like a cloud model's output.

  1) Install Ollama (ollama.com) and pull a model, e.g.:
        ollama pull qwen3.6:27b        # or gemma4:31b, qwen3.6:35b-a3b ... (check `ollama list` / ollama.com/library)
  2) python3 extract_local.py --vendor ExampleCo --model qwen3.6:27b
  3) python3 soc2_review.py verify --vendor ExampleCo --extraction Vendors/ExampleCo/extraction_local_<model>.json

The report is processed in passes (front matter, subservice/CUEC pages, then the control tables a few
pages at a time) so it fits a local model's context window. Pages are tagged <<<PDF PAGE n>>> so the
model can cite them; the verifier then checks every quote against the PDF.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from soc2v2.pdftext import PdfDoc  # noqa: E402
from soc2v2.paths import report_pdf, vendor_dir as resolve_vendor  # noqa: E402
from soc2v2.verifier import _control_pages  # noqa: E402

SCHEMA = json.load(open(os.path.join(HERE, "soc2v2", "schema", "extraction.schema.json")))
PROMPT = open(os.path.join(HERE, "soc2v2", "prompts", "extraction_prompt.md")).read()


def subschema(props, required):
    return {"type": "object", "$defs": SCHEMA["$defs"],
            "properties": {k: SCHEMA["properties"][k] for k in props}, "required": required}


STATS = {"llm_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0}


def call_ollama(host, model, system, user, schema, num_ctx, timeout):
    body = {"model": model, "stream": False, "format": schema,
            "options": {"temperature": 0, "num_ctx": num_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "think": False}
    for attempt in (1, 2):
        req = urllib.request.Request(f"{host}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            STATS["llm_calls"] += 1
            STATS["prompt_tokens"] += out.get("prompt_eval_count", 0) or 0
            STATS["output_tokens"] += out.get("eval_count", 0) or 0
            STATS["llm_seconds"] += (out.get("total_duration", 0) or 0) / 1e9
            return json.loads(out["message"]["content"])
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if attempt == 1 and "think" in msg.lower():
                body.pop("think")          # model/server without thinking support
                continue
            raise SystemExit(f"Ollama error {e.code}: {msg[:400]}")
        except urllib.error.URLError as e:
            raise SystemExit(f"Cannot reach Ollama at {host} ({e.reason}). Is `ollama serve` running?")
        except json.JSONDecodeError as e:
            if attempt == 1:
                continue
            raise SystemExit(f"Model returned invalid JSON: {e}")


SIDE_TERMS = [(r"complementary user entity", 3), (r"user entity responsibilit", 3),
              (r"carved[- ]out|carve-out|exclude[sd]? .{0,40}subservice", 3),
              (r"subservice organi[sz]ation", 2), (r"complementary subservice", 2),
              (r"(?:user entit(?:y|ies)|customers?) (?:are|is) responsible", 1)]
SIDE_ASK = ("Extract ONLY `subservice_orgs` and `cuecs` from these pages. CUECs may be titled 'complementary user "
            "entity controls', 'user entity responsibilities' or 'customer responsibilities'; record each one. If the "
            "report names no subservice organizations, return an empty subservice_orgs list.\n\n")


def side_pages(doc, before, cap=12):
    """Pages before the testing section most likely to describe subservice orgs and CUECs (capped for context)."""
    scored = []
    for i in range(1, min(before, doc.n_pages + 1)):
        t = doc.norm[i - 1]
        sc = sum(w * len(re.findall(p, t)) for p, w in SIDE_TERMS)
        if sc:
            scored.append((sc, i))
    return sorted(i for _, i in sorted(scored, reverse=True)[:cap])


def tagged(doc, pages):
    return "\n\n".join(f"<<<PDF PAGE {p}>>>\n{doc.raw[p - 1]}" for p in pages)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--model", required=True, help="Ollama model tag, e.g. qwen3.6:27b")
    ap.add_argument("--pdf", help="SOC report file name inside the vendor folder")
    ap.add_argument("--password", help="PDF open password, if the vendor supplied one")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--num-ctx", type=int, default=32768, help="context window to request (tokens)")
    ap.add_argument("--window", type=int, default=2, help="control-table pages per request")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per request")
    a = ap.parse_args()

    try:
        vdir = resolve_vendor(a.vendor)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    pdf = report_pdf(vdir, a.pdf)
    doc = PdfDoc(pdf, a.password)
    if doc.no_text_pages:
        print(f"[!] {len(doc.no_text_pages)} pages have no text layer (scanned?). OCR first: ocrmypdf in.pdf out.pdf")

    ctrl = _control_pages(doc)
    first_ctrl = ctrl[0] if ctrl else doc.n_pages + 1
    front = list(range(1, max(2, min(first_ctrl, 11))))
    sub = side_pages(doc, first_ctrl)
    system = PROMPT + "\n\nPages are marked <<<PDF PAGE n>>>. Use n as pdf_page. Copy quotes exactly as written."
    t0 = time.time()
    print(f"[*] {os.path.basename(pdf)}: {doc.n_pages} pages; control tables on PDF p.{ctrl[0] if ctrl else '?'}-{ctrl[-1] if ctrl else '?'}")

    print(f"[1/3] report facts and opinion (pages {front[0]}-{front[-1]})...")
    r1 = call_ollama(a.host, a.model, system,
                     "Extract ONLY the `report` object from these pages.\n\n" + tagged(doc, front),
                     subschema(["report"], ["report"]), a.num_ctx, a.timeout)

    print(f"[2/3] subservice orgs and CUECs (pages {sub})...")
    r2 = call_ollama(a.host, a.model, system,
                     SIDE_ASK + tagged(doc, sub),
                     subschema(["subservice_orgs", "cuecs"], ["subservice_orgs", "cuecs"]), a.num_ctx, a.timeout) if sub else {}

    controls, exceptions = {}, []
    windows = [ctrl[i:i + a.window] for i in range(0, len(ctrl), a.window)]
    for n, w in enumerate(windows, 1):
        print(f"[3/3] controls, window {n}/{len(windows)} (PDF p.{w[0]}-{w[-1]})...")
        r3 = call_ollama(a.host, a.model, system,
                         "Extract ONLY `controls` and `exceptions` from these pages. A control may start on an earlier "
                         "page; include any control whose ID or tests appear here.\n\n" + tagged(doc, w),
                         subschema(["controls", "exceptions"], ["controls", "exceptions"]), a.num_ctx, a.timeout)
        for c in r3.get("controls", []):
            cid = c.get("id")
            if not cid:
                continue
            if cid not in controls:
                controls[cid] = c
            else:   # control split across windows: merge tests, de-duplicated
                seen = {re.sub(r"\W+", "", t.get("procedure", "").lower())[:80] for t in controls[cid]["tests"]}
                for t in c.get("tests", []):
                    k = re.sub(r"\W+", "", t.get("procedure", "").lower())[:80]
                    if k not in seen:
                        controls[cid]["tests"].append(t)
                        seen.add(k)
        exceptions += r3.get("exceptions", [])

    out = {
        "schema_version": "1.0",
        "extractor": {"model": a.model, "tool": f"Ollama (local) via extract_local.py, host {a.host}",
                      "run_at": dt.datetime.now().isoformat(timespec="seconds"),
                      "notes": f"Local extraction; {len(windows)} control windows of {a.window} pages; "
                               f"{time.time() - t0:.0f}s total.",
                      "stats": {"method": "A: full transcription", "wall_seconds": round(time.time() - t0, 1),
                                **{k: round(v, 1) if isinstance(v, float) else v for k, v in STATS.items()}}},
        "source": {"file": os.path.basename(pdf), "sha256": doc.sha256, "page_count": doc.n_pages},
        "report": r1.get("report", {}),
        "subservice_orgs": r2.get("subservice_orgs", []),
        "cuecs": r2.get("cuecs", []),
        "controls": list(controls.values()),
        "exceptions": exceptions,
        "claims": [],
    }
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", a.model)
    path = os.path.join(vdir, f"extraction_local_{safe}.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"[✓] {len(controls)} controls, {len(out['cuecs'])} CUECs, {len(out['subservice_orgs'])} subservice orgs "
          f"in {time.time() - t0:.0f}s -> {os.path.relpath(path, HERE)}")
    print(f"    Next: python3 soc2_review.py verify --vendor {a.vendor} --extraction {os.path.relpath(path, HERE)}")


if __name__ == "__main__":
    main()
