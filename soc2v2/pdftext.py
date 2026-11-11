"""
PDF text utilities for the SOC 2 review verifier.

Everything in this module is deterministic: same PDF in, same answers out.
No AI is used here. This is the "checker" layer that audits the AI's work.
"""

import hashlib
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

import pypdf


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"',
           "–": "-", "—": "-", "−": "-", " ": " "}


def normalize(text):
    """Normalize text so quotes match regardless of PDF extraction quirks."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    for a, b in _QUOTES.items():
        t = t.replace(a, b)
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)   # re-join hyphenated line breaks
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([.,;:)])", r"\1", t)          # "Company ." -> "Company."
    return t.strip().lower()


class PdfDoc:
    """A loaded PDF with per-page raw text, normalized text and page labels."""

    def __init__(self, path, password=None):
        self.path = path
        self.sha256 = sha256_file(path)
        reader = pypdf.PdfReader(path)
        self.was_encrypted = reader.is_encrypted
        if reader.is_encrypted:
            # Owner-password-only PDFs (copy/print restrictions) open with an empty user password.
            # Open-password PDFs need the password the vendor gave you.
            if not reader.decrypt(password or ""):
                raise PermissionError(
                    f"{path} is password-protected. Re-run with --password, or open it yourself and save "
                    f"an unlocked copy (if the vendor's terms allow).")
        self.raw = [(p.extract_text() or "") for p in reader.pages]
        # pages with (almost) no text layer are scans/images: they need OCR before AI or code can read them
        self.no_text_pages = [i for i, t in enumerate(self.raw, 1) if len(t.strip()) < 40]
        self.norm = [normalize(t) for t in self.raw]
        self.n_pages = len(self.raw)
        self.printed_offset = self._detect_printed_offset()

    # ---- page numbering -------------------------------------------------
    def _detect_printed_offset(self):
        """
        Reports usually print their own page numbers, which rarely match the PDF
        viewer's page index (cover pages, unnumbered title pages). Find the most
        common difference between PDF index and a number printed at the top or
        bottom of each page. Returns offset such that printed = pdf - offset.
        """
        offsets = Counter()
        for i, raw in enumerate(self.raw, start=1):
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            edge = " ".join(lines[:3] + lines[-3:])
            for m in re.finditer(r"(?:^|[\s/|-])(\d{1,3})(?=\s|$)", edge):
                n = int(m.group(1))
                if 0 < n <= self.n_pages:
                    offsets[i - n] += 1
        if not offsets:
            return None
        offset, votes = offsets.most_common(1)[0]
        return offset if votes >= max(3, self.n_pages // 4) else None

    def printed_page(self, pdf_page):
        if self.printed_offset is None:
            return None
        n = pdf_page - self.printed_offset
        return n if n > 0 else None

    def pdf_page_from_printed(self, printed):
        if self.printed_offset is None:
            return None
        p = printed + self.printed_offset
        return p if 1 <= p <= self.n_pages else None

    def page_ref(self, pdf_page):
        pp = self.printed_page(pdf_page)
        return f"PDF p.{pdf_page}" + (f" (printed p.{pp})" if pp else "")

    # ---- searching ------------------------------------------------------
    def pages_containing(self, needle, regex=False):
        if regex:
            pat = re.compile(needle, re.I)
            return [i for i, t in enumerate(self.norm, 1) if pat.search(t)]
        n = normalize(needle)
        return [i for i, t in enumerate(self.norm, 1) if n and n in t]

    def find_quote(self, quote, pdf_page=None, fuzzy_threshold=0.85):
        """
        Locate a quote. Returns dict(status, found_pages, score).
        status: exact | exact_other_page | approximate | approximate_other_page | not_found
        """
        q = normalize(quote)
        if not q:
            return {"status": "not_found", "found_pages": [], "score": 0.0}
        exact = [i for i, t in enumerate(self.norm, 1) if q in t]
        if pdf_page and pdf_page in exact:
            return {"status": "exact", "found_pages": [pdf_page], "score": 1.0}
        if exact:
            return {"status": "exact_other_page", "found_pages": exact, "score": 1.0}
        # PDF text extraction often splits words ("co ntrols", "neve rtheless").
        # Compare with all whitespace removed before falling back to fuzzy matching.
        qs = q.replace(" ", "")
        squashed = [i for i, t in enumerate(self.norm, 1) if qs in t.replace(" ", "")]
        if pdf_page and pdf_page in squashed:
            return {"status": "exact", "found_pages": [pdf_page], "score": 1.0, "note": "spacing-insensitive"}
        if squashed:
            return {"status": "exact_other_page", "found_pages": squashed, "score": 1.0}
        # fuzzy: longest common block relative to quote length
        best = []
        for i, t in enumerate(self.norm, 1):
            if not t:
                continue
            sm = SequenceMatcher(None, q, t, autojunk=False)
            m = sm.find_longest_match(0, len(q), 0, len(t))
            # extend: compare the quote against the window around the match
            start = max(0, m.b - m.a)
            window = t[start:start + len(q) + 20]
            score = SequenceMatcher(None, q, window, autojunk=False).ratio()
            best.append((score, i))
        best.sort(reverse=True)
        if best and best[0][0] >= fuzzy_threshold:
            score, page = best[0]
            same = pdf_page == page
            return {"status": "approximate" if same else "approximate_other_page",
                    "found_pages": [page], "score": round(score, 3)}
        return {"status": "not_found", "found_pages": [],
                "score": round(best[0][0], 3) if best else 0.0}

    def context(self, pdf_page, start, end, pad=120):
        t = self.norm[pdf_page - 1]
        return t[max(0, start - pad):min(len(t), end + pad)]
