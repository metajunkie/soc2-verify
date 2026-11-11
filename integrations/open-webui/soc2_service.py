#!/usr/bin/env python3
"""
SOC 2 review service: a small local HTTP API that lets an Open WebUI assistant (e.g. running in Docker/OrbStack) run the
SOC 2 review pipeline on this Mac. Vendor reports stay in SOC-2-Reviews on the Mac; the Open WebUI
container never sees the PDFs, only short summaries.

Endpoints (all require  Authorization: Bearer <token from integrations/open-webui/.token>):
  GET  /health
  GET  /vendors                       vendor folders under Vendors/, their extractions, latest verification result
  POST /verify   {vendor, extraction?, ccm?}   run the deterministic verifier (seconds)
  POST /extract  {vendor, model, method} start a local-model extraction + verification (background job);
                                       method: hybrid (default) | hybrid-all | full
  GET  /jobs/<id>                      job status / result
  GET  /activity                      work-activity metadata only (vendors, counts, dates; never findings)

Start:  ./start_service.sh      Stop: ./stop_service.sh      Port: 8765 (SOC2_PORT), bind 127.0.0.1 (SOC2_BIND)
Standard library only; runs the pipeline with this folder's Python.
"""
import glob
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))   # repo root (this file lives in integrations/open-webui/)
sys.path.insert(0, ROOT)
from soc2v2.paths import list_vendors, vendor_dir  # noqa: E402
PY = sys.executable
TOKEN = open(os.path.join(HERE, ".token")).read().strip()
BIND = os.environ.get("SOC2_BIND", "127.0.0.1")
PORT = int(os.environ.get("SOC2_PORT", "8765"))
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,80}$")
JOBS, LOCK = {}, threading.Lock()


def vendor_dirs():
    return list_vendors()


def check_vendor(v):
    if v not in vendor_dirs():            # whitelist: no paths, no traversal
        raise ValueError(f"Unknown vendor '{v}'. Known: {', '.join(vendor_dirs())}")
    return v


def extractions(v):
    files = sorted(glob.glob(os.path.join(vendor_dir(v), "extraction*.json")), key=os.path.getmtime, reverse=True)
    return [os.path.basename(f) for f in files if "REDTEAM" not in f]


def latest_verification(v):
    files = glob.glob(os.path.join(vendor_dir(v), "Review_*", "v2", "Verification_*.json"))
    return max(files, key=os.path.getmtime) if files else None


def summarize(path, limit=25):
    d = json.load(open(path))
    meta, items = d["meta"], d["results"]
    open_items = [i for i in items if i["status"] in ("FAIL", "REVIEW")]
    return {
        "overall": meta["overall"], "counts": meta["counts"], "review_date": meta["review_date"],
        "extracted_by": meta.get("extractor"), "source_pdf_sha256": meta["source_pdf_sha256"],
        "report_file": os.path.relpath(path.replace(".json", ".md"), ROOT),
        "items": [{"status": i["status"], "section": i["section"], "check": i["check"], "detail": str(i["detail"])[:300],
                   "disposition": (i.get("disposition") or {}).get("decision")} for i in open_items[:limit]],
        "more_items": max(0, len(open_items) - limit),
    }


def run_verify(v, extraction=None, ccm=False):
    if extraction:
        if os.path.basename(extraction) != extraction or extraction not in extractions(v):
            raise ValueError(f"Unknown extraction '{extraction}'. Available: {', '.join(extractions(v)) or 'none'}")
    else:
        ex = extractions(v)
        if not ex:
            raise ValueError(f"No extraction for {v} yet. Start one with a local model first.")
        extraction = "extraction.json" if "extraction.json" in ex else ex[0]
    cmd = [PY, "soc2_review.py", "verify", "--vendor", v, "--extraction", os.path.relpath(os.path.join(vendor_dir(v), extraction), ROOT)]
    if ccm:
        cmd += ["--overlay", "ccm"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    m = re.search(r"JSON\s*:\s*(\S.*\.json)", p.stdout)
    if p.returncode != 0 or not m:
        raise RuntimeError((p.stderr or p.stdout)[-1500:])
    out = summarize(os.path.join(ROOT, m.group(1).strip()))
    out["extraction"] = extraction
    return out


METHODS = {"hybrid": ["extract_hybrid.py", "--criteria", "gaps"],
           "hybrid-all": ["extract_hybrid.py", "--criteria", "all"],
           "full": ["extract_local.py"]}


def extract_job(job_id, v, model, method="hybrid"):
    def upd(**k):
        with LOCK:
            JOBS[job_id].update(k)
    try:
        upd(status="extracting", started=time.time())
        p = subprocess.run([PY, *METHODS[method], "--vendor", v, "--model", model], cwd=ROOT,
                           capture_output=True, text=True, timeout=4 * 3600)
        m = re.search(r"-> (\S+\.json)", p.stdout)
        if p.returncode != 0 or not m:
            raise RuntimeError((p.stderr or p.stdout)[-1500:])
        upd(status="verifying", extraction_log=p.stdout[-800:])
        result = run_verify(v, os.path.basename(m.group(1)))
        upd(status="done", result=result, finished=time.time())
    except Exception as e:  # noqa: BLE001
        upd(status="failed", error=str(e)[-1500:], finished=time.time())


def activity():
    """Work-activity metadata for conversation: vendor names, review counts, dates. Never findings or verdicts."""
    import datetime as dt
    today = dt.date.today()
    week_start = today - dt.timedelta(days=today.weekday())
    last_week_start = week_start - dt.timedelta(days=7)
    reviews, vendors = [], []
    for v in vendor_dirs():
        vd = vendor_dir(v)
        days = set()
        for f in glob.glob(os.path.join(vd, "Review_*", "v2", "Verification_*.json")):
            days.add(dt.date.fromtimestamp(os.path.getmtime(f)))
        period_end = None
        for f in sorted(glob.glob(os.path.join(vd, "extraction*.json")), key=os.path.getmtime, reverse=True):
            if "REDTEAM" in f:
                continue
            try:
                pe = (json.load(open(f)).get("report") or {}).get("period_end")
                if pe:
                    period_end = dt.date.fromisoformat(pe)
                    break
            except Exception:  # noqa: BLE001
                pass
        last = max(days) if days else None
        vendors.append({"vendor": v, "days_worked": len(days), "last_worked": str(last) if last else None,
                        "report_period_end": str(period_end) if period_end else None,
                        "report_age_months": round((today - period_end).days / 30.4, 1) if period_end else None})
        reviews += [(v, d) for d in days]
    return {
        "today": str(today),
        "vendors": vendors,
        "vendor_count": len(vendors),
        "review_sessions_this_week": sum(1 for _, d in reviews if d >= week_start),
        "review_sessions_last_week": sum(1 for _, d in reviews if last_week_start <= d < week_start),
        "vendors_worked_this_week": sorted({v for v, d in reviews if d >= week_start}),
        "running_jobs": [{"vendor": j["vendor"], "status": j["status"]} for j in JOBS.values()
                         if j["status"] in ("queued", "extracting", "verifying")],
        "note": "Metadata only: no findings, verdicts or report content.",
    }


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _auth(self):
        if self.headers.get("Authorization", "") != f"Bearer {TOKEN}":
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def log_message(self, fmt, *args):          # log method + path only, never bodies
        sys.stderr.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self.command} {self.path}\n")

    def do_GET(self):
        if not self._auth():
            return
        try:
            if self.path == "/health":
                return self._send(200, {"ok": True, "root": ROOT})
            if self.path == "/activity":
                return self._send(200, activity())
            if self.path == "/vendors":
                out = []
                for v in vendor_dirs():
                    lv = latest_verification(v)
                    s = summarize(lv) if lv else None
                    out.append({"vendor": v, "extractions": extractions(v),
                                "latest": {k: s[k] for k in ("overall", "counts", "review_date")} if s else None})
                return self._send(200, {"vendors": out})
            m = re.match(r"^/jobs/([0-9a-f-]{36})$", self.path)
            if m:
                with LOCK:
                    j = JOBS.get(m.group(1))
                return self._send(200 if j else 404, j or {"error": "no such job"})
            self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(400, {"error": str(e)})

    def do_POST(self):
        if not self._auth():
            return
        try:
            b = self._body()
            if self.path == "/verify":
                return self._send(200, run_verify(check_vendor(b.get("vendor", "")), b.get("extraction") or None,
                                                  bool(b.get("ccm"))))
            if self.path == "/extract":
                v, model = check_vendor(b.get("vendor", "")), b.get("model", "")
                method = b.get("method") or "hybrid"
                if not MODEL_RE.match(model):
                    raise ValueError("Give an Ollama model tag, e.g. gemma4:31b")
                if method not in METHODS:
                    raise ValueError(f"method must be one of: {', '.join(METHODS)}")
                with LOCK:
                    busy = [j for j in JOBS.values() if j["status"] in ("queued", "extracting", "verifying")]
                if busy:
                    raise ValueError(f"An extraction is already running (job {busy[0]['id']}).")
                jid = str(uuid.uuid4())
                with LOCK:
                    JOBS[jid] = {"id": jid, "vendor": v, "model": model, "method": method, "status": "queued",
                                 "created": time.time()}
                threading.Thread(target=extract_job, args=(jid, v, model, method), daemon=True).start()
                return self._send(202, JOBS[jid])
            self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(400, {"error": str(e)})


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer((BIND, PORT), H)
    print(f"SOC 2 review service on http://{BIND}:{PORT}  (root {ROOT})", flush=True)
    srv.serve_forever()
