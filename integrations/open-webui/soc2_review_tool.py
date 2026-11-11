"""
title: SOC 2 Review
author: the user Walling (CyberCede)
version: 0.3.0
description: Run and summarize SOC 2 Type II vendor reviews through the local SOC 2 review service on this Mac. Vendor reports never enter Open WebUI; only findings summaries do.
"""

import json
import urllib.error
import urllib.request

from pydantic import BaseModel, Field

NOTE = ("\n\n[Confidential vendor-assurance content under NDA. Do not save these findings to memory. "
        "The verifier flags issues; the human reviewer makes the risk decision.]")


class Tools:
    class Valves(BaseModel):
        SERVICE_URL: str = Field(default="http://host.docker.internal:8765",
                                 description="SOC 2 review service on the Mac (from inside OrbStack)")
        API_TOKEN: str = Field(default="", description="Contents of integrations/open-webui/.token")
        DEFAULT_MODEL: str = Field(default="gemma4:26b", description="Ollama model for local extraction")
        TIMEOUT_SECONDS: int = Field(default=600)

    def __init__(self):
        self.valves = self.Valves()

    def _call(self, method: str, path: str, body: dict = None) -> dict:
        req = urllib.request.Request(
            self.valves.SERVICE_URL.rstrip("/") + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.valves.API_TOKEN.strip()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.valves.TIMEOUT_SECONDS) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return {"error": json.loads(e.read()).get("error", str(e))}
            except Exception:
                return {"error": str(e)}
        except urllib.error.URLError as e:
            return {"error": f"The SOC 2 review service is not running ({e.reason}). Tell the user to start it by running "
                             f"this in a terminal on the machine with the reports: <repo>/integrations/open-webui/start_service.sh "
                             f"then ask again."}
        except Exception as e:
            return {"error": f"SOC 2 tool error: {e}"}

    @staticmethod
    def _fmt(r: dict) -> str:
        if "error" in r:
            return "Error: " + r["error"]
        c = r.get("counts", {})
        lines = [f"Overall: {r.get('overall')}  (FAIL {c.get('FAIL', 0)}, REVIEW {c.get('REVIEW', 0)}, PASS {c.get('PASS', 0)})",
                 f"Extraction: {r.get('extraction', '?')}  |  Review date: {r.get('review_date')}",
                 f"Full report on the Mac: SOC-2-Reviews/{r.get('report_file')}", "", "Items needing the reviewer:"]
        for i in r.get("items", []):
            d = f" -> {i['disposition']}" if i.get("disposition") else ""
            lines.append(f"- {i['status']}{d} | {i['section']} | {i['check']}: {i['detail']}")
        if r.get("more_items"):
            lines.append(f"... and {r['more_items']} more in the full report.")
        return "\n".join(lines) + NOTE

    def list_soc2_vendors(self) -> str:
        """
        List vendors that have SOC 2 reports in the review folder, their available extractions, and the result of
        the most recent verification run. Use this when asked which vendor reviews exist or their status.
        If the result says the service is not running, give the user the exact start command from the result.
        """
        r = self._call("GET", "/vendors")
        if "error" in r:
            return "Error: " + r["error"]
        out = []
        for v in r.get("vendors", []):
            lt = v.get("latest") or {}
            out.append(f"- {v['vendor']}: extractions {', '.join(v['extractions']) or 'none'}; "
                       f"latest result {lt.get('overall', 'not yet verified')} ({lt.get('review_date', '-')})")
        return "Vendors:\n" + "\n".join(out) if out else "No vendor folders with SOC reports found."

    def verify_soc2_review(self, vendor: str, extraction: str = "", include_ccm: bool = False) -> str:
        """
        Run the deterministic verifier on a vendor's SOC 2 extraction and summarize the findings (takes seconds).
        :param vendor: Vendor folder name exactly as listed, e.g. "ExampleCo".
        :param extraction: Optional extraction file name; leave empty for the default/latest.
        :param include_ccm: Also produce the CSA CCM coverage view.
        """
        return self._fmt(self._call("POST", "/verify", {"vendor": vendor, "extraction": extraction or None,
                                                        "ccm": include_ccm}))

    def start_soc2_extraction(self, vendor: str, model: str = "", method: str = "hybrid") -> str:
        """
        Start reading a vendor's SOC 2 report with a LOCAL model (nothing leaves the Mac), then verify the result.
        This runs in the background; it returns a job ID to check later.
        :param vendor: Vendor folder name exactly as listed, e.g. "ExampleCo".
        :param model: Optional Ollama model tag; defaults to the configured model.
        :param method: "hybrid" (default, fast: code reads the control tables, the model answers only targeted
                       questions), "hybrid-all" (also asks the model about every criterion as a cross-check), or
                       "full" (the model transcribes the whole report; slowest). Use the default unless the user asks.
        """
        r = self._call("POST", "/extract", {"vendor": vendor, "model": model or self.valves.DEFAULT_MODEL,
                                             "method": method or "hybrid"})
        if "error" in r:
            return "Error: " + r["error"]
        return (f"Started {r.get('method', method)} extraction for {vendor} with {r['model']}. Job ID: {r['id']}. "
                f"Ask me to check this job in a few minutes.")

    def get_soc2_work_activity(self) -> str:
        """
        Get a light summary of the vendor-review work the user and you have been doing together: which vendors have been
        reviewed, how many review sessions this week and last week, when each vendor was last worked on, how old each
        vendor's SOC report is, and any extraction still running. Contains no findings.
        Use it when the user brings up work, asks what you've been working on together, or a casual check-in about the
        workday fits the moment. Mention it conversationally and briefly, at most once per conversation; never nag,
        never list findings, and drop the topic if the user moves on.
        """
        r = self._call("GET", "/activity")
        if "error" in r:
            return "Error: " + r["error"]
        lines = [f"Today: {r['today']}. Vendors on file: {r['vendor_count']}.",
                 f"Review sessions this week: {r['review_sessions_this_week']} "
                 f"({', '.join(r['vendors_worked_this_week']) or 'none'}); last week: {r['review_sessions_last_week']}."]
        for v in r["vendors"]:
            age = f"; report is {v['report_age_months']} months past its period end" if v.get("report_age_months") is not None else ""
            lines.append(f"- {v['vendor']}: worked on {v['days_worked']} day(s), last {v['last_worked'] or 'never'}{age}")
        for j in r.get("running_jobs", []):
            lines.append(f"Still running: {j['vendor']} extraction ({j['status']}).")
        return "\n".join(lines) + "\n[Work context only. No findings; fine to mention conversationally.]"

    def check_soc2_job(self, job_id: str) -> str:
        """
        Check the status of a SOC 2 extraction job started earlier, and summarize the verification when it is done.
        :param job_id: The job ID returned when the extraction was started.
        """
        r = self._call("GET", f"/jobs/{job_id.strip()}")
        if "error" in r:
            return "Error: " + r["error"]
        if r.get("status") == "done":
            return f"Job finished ({r['vendor']}, {r['model']}).\n" + self._fmt(r["result"])
        if r.get("status") == "failed":
            return f"Job failed: {r.get('error', '')[-600:]}"
        return f"Job {r['id']} is {r['status']} ({r['vendor']}, {r['model']}, {r.get('method', 'full')})."
