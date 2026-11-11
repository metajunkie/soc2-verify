"""
Folder layout for the SOC 2 review workspace (one place, used by every script).

SOC-2-Reviews/
  Vendors/<Vendor>/            one folder per vendor: SOC report PDF, ISO certs, profile, extractions, reviews
  soc2v2/                      code, schema, prompts, criteria, profile template
  angel_tool/                  Open WebUI tool + local review service
  *.py, INSTRUCTIONS.md        entry points and the SOP
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDORS = os.path.join(ROOT, "Vendors")


def _safe(name):
    if not name or name.startswith(".") or "/" in name or "\\" in name or name in ("..",):
        raise ValueError(f"Invalid vendor name: {name!r}")
    return name


def is_vendor_folder(path):
    pdfs = [os.path.basename(f).lower() for f in glob.glob(os.path.join(path, "*.pdf"))]
    return any("soc" in f for f in pdfs) or os.path.exists(os.path.join(path, "scope_profile.yaml"))


def vendor_dir(name, must_exist=True):
    """Vendors/<name>. Falls back to a legacy top-level <name> folder, with a warning."""
    p = os.path.join(VENDORS, _safe(name))
    if os.path.isdir(p):
        return p
    legacy = os.path.join(ROOT, name)
    if os.path.isdir(legacy) and is_vendor_folder(legacy):
        print(f"[!] Using legacy location {legacy}; move it to Vendors/{name}/", file=sys.stderr)
        return legacy
    if must_exist:
        raise FileNotFoundError(f"Vendor folder not found: {p}. Create it and put the vendor's SOC report PDF inside.")
    return p


def list_vendors():
    if not os.path.isdir(VENDORS):
        return []
    return [d for d in sorted(os.listdir(VENDORS))
            if not d.startswith(".") and os.path.isdir(os.path.join(VENDORS, d)) and is_vendor_folder(os.path.join(VENDORS, d))]


def report_pdf(vdir, hint=None):
    """The vendor's SOC report: not a bridge letter, ISO certificate or other attachment. Largest SOC-named PDF wins."""
    if hint:
        return os.path.join(vdir, hint)
    pdfs = [f for f in os.listdir(vdir) if f.lower().endswith(".pdf")
            and not any(x in f.lower() for x in ("bridge", "gap letter", "iso", "certificate"))]
    if not pdfs:
        raise FileNotFoundError(f"No SOC report PDF found in {vdir}")
    pdfs.sort(key=lambda f: ("soc" in f.lower(), os.path.getsize(os.path.join(vdir, f))), reverse=True)
    return os.path.join(vdir, pdfs[0])
