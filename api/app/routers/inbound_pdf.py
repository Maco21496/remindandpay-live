# FINAL VERSION OF api/app/routers/inbound_pdf.py
from __future__ import annotations
import io
import re
from typing import Dict, Any, List, Optional, Tuple

import pdfplumber
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from ..shared import APIRouter as _APIRouter  # keep import style consistent with your project
from .auth import require_user

import traceback
import sys

router = APIRouter(prefix="/api/inbound/pdf", tags=["inbound-pdf-preview"])

# -----------------------------
# Utilities
# -----------------------------

_WS_RE = re.compile(r"[ \t]+")
_NEWLINE_SQUASH_RE = re.compile(r"\n{2,}")  # collapse massive gaps

_AMOUNT_RE = r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2}))"
_MONTH_WORD = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?)|Dec(?:ember)?"
_DATE_NUM   = r"\d{2}/\d{2}/\d{4}"
_DATE_WORD  = rf"\d{{1,2}}\s+{_MONTH_WORD}\s+\d{{4}}"
_DATE_RE    = rf"({_DATE_NUM}|{_DATE_WORD})"
_INV_RE     = r"([A-Z0-9][A-Z0-9\-\/\.]*)"

def _clean_text(s: str) -> str:
    s = s.replace("\r", "")
    s = s.replace("£", " GBP ").replace("(E)", " GBP ")
    s = _WS_RE.sub(" ", s)
    return s.strip()

def _extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        texts: List[str] = []
        for page in pdf.pages:
            t = page.extract_text() or ""
            texts.append(t)
        full = "\n".join(texts)
    full = full.replace("\r", "")
    return full

def _find_first(patterns: List[re.Pattern], text: str) -> Optional[str]:
    for rx in patterns:
        m = rx.search(text)
        if m:
            return m.group(1).strip()
    return None

def _find_all_amounts_after_total(text: str) -> List[float]:
    results: List[float] = []
    rx = re.compile(r"(?i)\btotal\b[^0-9\-]*" + _AMOUNT_RE)
    for m in rx.finditer(text):
        try:
            amt = float(m.group(1).replace(",", ""))
            results.append(amt)
        except Exception:
            pass
    return results

def _detect_currency(text: str) -> Optional[str]:
    up = text.upper()
    if "GBP" in up or "£" in text or " GBP " in up:
        return "GBP"
    if "EUR" in up or "€" in text:
        return "EUR"
    if "USD" in up or "$" in text:
        return "USD"
    return None

def _capture_on_same_line(text: str, anchor: str, capture_re: str, case_ins: bool) -> Optional[str]:
    flags = re.IGNORECASE if case_ins else 0
    esc = re.escape(anchor)
    rx = re.compile(esc + r"[^\n\r]*?" + capture_re, flags)
    m = rx.search(text)
    if m:
        return m.group(1).strip()
    return None

# ---------- geometry helpers ----------

def _group_words_into_lines(words: List[Dict[str, Any]], y_tol: float = 3.0) -> List[Dict[str, Any]]:
    """
    Group words into visual lines by proximity of 'top'. Returns list of dicts:
      { 'text': 'line text', 'x0': float_min, 'x1': float_max, 'top': avg_top, 'bottom': avg_bottom }
    """
    if not words:
        return []
    words = sorted(words, key=lambda w: (w.get("top", 0.0), w.get("x0", 0.0)))
    lines: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = [words[0]]
    for w in words[1:]:
        if abs(w.get("top", 0.0) - current[-1].get("top", 0.0)) <= y_tol:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
    if current:
        lines.append(current)

    out: List[Dict[str, Any]] = []
    for ln in lines:
        ordered = sorted(ln, key=lambda u: u.get("x0", 0.0))
        text = " ".join(x.get("text", "") for x in ordered)
        x0 = min(u.get("x0", 0.0) for u in ln)
        x1 = max(u.get("x1", 0.0) for u in ln)
        top = sum(u.get("top", 0.0) for u in ln) / len(ln)
        bottom = sum(u.get("bottom", u.get("top", 0.0)) for u in ln) / len(ln)
        out.append({"text": text.strip(), "x0": x0, "x1": x1, "top": top, "bottom": bottom})
    return out

def _normalize_token(s: str) -> str:
    # lower, strip punctuation/colon etc
    return re.sub(r"[^\w]+", "", s.lower())

def _split_anchor_tokens(anchor: str) -> List[str]:
    return [t for t in re.split(r"\s+", anchor.strip()) if t]

def _find_anchor_bbox(page: pdfplumber.page.Page, anchor: str, case_ins: bool) -> Optional[Tuple[float, float, float, float]]:
    """
    Locate the anchor text by matching its tokens against page words (case-insensitive, ignore punctuation).
    Returns (x0, top, x1, bottom) bbox of the anchor string.
    """
    tokens = _split_anchor_tokens(anchor)
    if not tokens:
        return None

    page_words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    if not page_words:
        return None

    norm_words = [(_normalize_token(w.get("text", "")), w) for w in page_words]
    norm_tokens = [_normalize_token(t) for t in tokens]

    i = 0
    while i < len(norm_words):
        t_idx = 0
        start_i = i
        while i < len(norm_words) and t_idx < len(norm_tokens):
            nw, w = norm_words[i]
            if (nw == norm_tokens[t_idx]) or (case_ins and nw.lower() == norm_tokens[t_idx].lower()):
                t_idx += 1
                i += 1
            else:
                start_i += 1
                i = start_i
                break
        if t_idx == len(norm_tokens):
            matched = [norm_words[k][1] for k in range(start_i, i)]
            x0 = min(w.get("x0", 0.0) for w in matched)
            x1 = max(w.get("x1", 0.0) for w in matched)
            top = min(w.get("top", 0.0) for w in matched)
            bottom = max(w.get("bottom", w.get("top", 0.0)) for w in matched)
            return (x0, top, x1, bottom)
    return None

# FINAL VERSION OF _clamp_to_page_bbox()
def _clamp_to_page_bbox(page, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    """
    Clamp a proposed (x0, y0, x1, y1) rectangle to the page bbox to avoid
    pdfplumber ValueError when using page.within_bbox(...).
    """
    pbx0, pby0, pbx1, pby1 = page.bbox  # (0.0, 0.0, width, height)
    cx0 = max(pbx0, min(x0, pbx1))
    cy0 = max(pby0, min(y0, pby1))
    cx1 = max(pbx0, min(x1, pbx1))
    cy1 = max(pby0, min(y1, pby1))
    if cx1 < cx0:
        cx0, cx1 = cx1, cx0
    if cy1 < cy0:
        cy0, cy1 = cy1, cy0
    return (cx0, cy0, cx1, cy1)

# FINAL VERSION OF _capture_on_next_line_geo()
def _capture_on_next_line_geo(pdf_bytes: bytes, anchor: str, capture_re: str, case_ins: bool) -> Optional[str]:
    """
    Geometry-aware 'next line': find the anchor on the page, then pick the nearest
    text line *below it on the same page* whose X-range overlaps the anchor’s X-range
    by at least 30%. This avoids stray characters at the left margin.
    """
    flags = re.IGNORECASE if case_ins else 0
    rx_cap = re.compile(capture_re, flags)

    def _overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
        inter = max(0.0, min(a1, b1) - max(a0, b0))
        width = min(max(0.0, a1 - a0), max(0.0, b1 - b0))
        return (inter / width) if width > 0 else 0.0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            # 1) find the anchor bbox (token-aware, tolerant of punctuation/case)
            bbox = _find_anchor_bbox(page, anchor, case_ins)
            if not bbox:
                continue
            ax0, atop, ax1, abottom = bbox

            # 2) build all page lines and select lines *below* the anchor
            words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            lines = _group_words_into_lines(words, y_tol=3.0)
            below = [ln for ln in lines if ln["top"] > (abottom + 0.5)]

            if not below:
                continue

            # 3) keep only lines whose horizontal span overlaps the anchor’s by ≥ 30%
            candidates = []
            for ln in below:
                if _overlap_ratio(ax0, ax1, ln["x0"], ln["x1"]) >= 0.30:
                    candidates.append(ln)

            if not candidates:
                continue

            # 4) take the nearest by vertical distance; run capture regex (or return the whole line)
            candidates.sort(key=lambda ln: ln["top"])
            line_text = candidates[0]["text"].strip()

            m = rx_cap.search(line_text)
            if m:
                return m.group(1).strip()
            if capture_re == r"(.+?)":
                return line_text

    return None

def _capture_on_next_line_text(text: str, anchor: str, capture_re: str, case_ins: bool) -> Optional[str]:
    """
    Text-only fallback: next non-empty line after the line that contains the anchor.
    """
    flags = re.IGNORECASE if case_ins else 0
    lines = text.replace("\r", "").split("\n")
    rx_anchor = re.compile(re.escape(anchor), flags)
    rx_cap = re.compile(capture_re, flags)
    for i, ln in enumerate(lines):
        if rx_anchor.search(ln):
            for j in range(i + 1, len(lines)):
                nxt = lines[j].strip()
                if not nxt:
                    continue
                m = rx_cap.search(nxt)
                if m:
                    return m.group(1).strip()
                return None
    return None

def _manual_capture(pdf_bytes: bytes, text_preserve_lines: str, anchor: Optional[str], mode: str, capture_re: str, case_ins: bool) -> Optional[str]:
    """
    mode: "same" or "next"
    """
    if not anchor:
        return None
    if mode == "next":
        v = _capture_on_next_line_geo(pdf_bytes, anchor, capture_re, case_ins)
        if v:
            return v
        return _capture_on_next_line_text(text_preserve_lines, anchor, capture_re, case_ins)
    return _capture_on_same_line(text_preserve_lines, anchor, capture_re, case_ins)

# -----------------------------
# Endpoint
# -----------------------------

# FINAL VERSION OF preview_pdf() WITH TRACEBACK LOGGING (drop-in replacement)
@router.post("/preview")
async def preview_pdf(
    file: UploadFile = File(...),

    # Manual anchors + modes (per-field)
    manual_invoice_number: Optional[str] = Form(None),
    manual_mode_invoice_number: str = Form("same"),  # "same" | "next"

    manual_issue_date: Optional[str] = Form(None),
    manual_mode_issue_date: str = Form("same"),

    manual_due_date: Optional[str] = Form(None),
    manual_mode_due_date: str = Form("same"),

    manual_amount_due: Optional[str] = Form(None),
    manual_mode_amount_due: str = Form("same"),

    manual_customer_name: Optional[str] = Form(None),
    manual_mode_customer_name: str = Form("same"),

    manual_case_insensitive: Optional[bool] = Form(True),

    user = Depends(require_user),
) -> Dict[str, Any]:
    """
    Accepts a single PDF file and returns a preview with detected fields.
    Priority per field: manual anchor (with chosen line mode) → regex heuristics.

    DIAGNOSTIC BUILD: prints full traceback to stderr on exception so you can see
    exactly which line in inbound_pdf.py failed.
    """
    try:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Please upload a PDF file.")

        raw = await file.read()
        if not raw:
            raise HTTPException(400, "Empty file.")

        try:
            raw_text = _extract_text(raw)
        except Exception as e:
            print("\n=== /api/inbound/pdf/preview: _extract_text failed ===", file=sys.stderr)
            traceback.print_exc()
            raise HTTPException(400, f"Could not read PDF text: {e}")

        # UI sample
        sample = raw_text.strip()
        if len(sample) > 4000:
            sample = sample[:4000] + "\n…(truncated)…"

        # Text variants
        text_preserve_lines = _clean_text(raw_text)  # keeps single \n
        text_single_line = _clean_text(_NEWLINE_SQUASH_RE.sub("\n", raw_text)).replace("\n", " ")
        case_ins = bool(manual_case_insensitive)

        # Regex fallbacks
        rx_invoice_number = [
            re.compile(r"(?i)\binvoice\s*number\s*[:\-]?\s*" + _INV_RE),
            re.compile(r"(?i)\binv(?:oice)?\s*no\.?\s*[:\-]?\s*" + _INV_RE),
        ]
        rx_issue_date = [
            re.compile(r"(?i)\binvoice\s*date\s*[:\-]?\s*" + _DATE_RE),
            re.compile(r"(?i)\border\s*date\s*[:\-]?\s*" + _DATE_RE),
            re.compile(r"(?i)\bdate\s*[:\-]?\s*" + _DATE_RE),
        ]
        rx_due_date = [
            re.compile(r"(?i)\bdue\s*date\s*[:\-]?\s*" + _DATE_RE),
            re.compile(r"(?i)\bpayment\s*due\s*[:\-]?\s*" + _DATE_RE),
        ]
        rx_customer_name = [
            re.compile(r"(?i)\bcustomer\s+(.+?)\s+invoice\s+number\s+[A-Z0-9\-\/\.]+"),
            re.compile(r"(?i)\bcustomer\s+(.+?)\s*(?:\n|$)"),
            re.compile(r"(?i)\b(?:bill(?:ed)?\s*to|invoice\s*to|invoiced\s*to)\s*[:\-]?\s*(.+)"),
        ]

        notes: List[str] = []
        used_manual: Dict[str, bool] = {}

        # Customer name
        customer_name = _manual_capture(
            raw, text_preserve_lines, manual_customer_name, manual_mode_customer_name, r"(.+?)", case_ins
        )
        if customer_name:
            used_manual["customer_name"] = True
            customer_name = customer_name.split("\n", 1)[0].strip()
        else:
            customer_name = _find_first(rx_customer_name, text_single_line) or ""

        # Invoice number
        invoice_number = _manual_capture(
            raw, text_preserve_lines, manual_invoice_number, manual_mode_invoice_number, _INV_RE, case_ins
        )
        if invoice_number:
            used_manual["invoice_number"] = True
        if not invoice_number:
            invoice_number = _find_first(rx_invoice_number, text_single_line)
        if not invoice_number:
            notes.append("Couldn’t confidently detect the invoice number.")
        if invoice_number:
            invoice_number = re.sub(r"\D+", "", invoice_number)  # digits-only

        # Issue date
        issue_date = _manual_capture(
            raw, text_preserve_lines, manual_issue_date, manual_mode_issue_date, _DATE_RE, case_ins
        )
        if issue_date:
            used_manual["issue_date"] = True
        if not issue_date:
            issue_date = _find_first(rx_issue_date, text_single_line) or ""

        # Due date
        due_date = _manual_capture(
            raw, text_preserve_lines, manual_due_date, manual_mode_due_date, _DATE_RE, case_ins
        )
        if due_date:
            used_manual["due_date"] = True
        if not due_date:
            due_date = _find_first(rx_due_date, text_single_line) or ""

        # Amount due
        amount_due = _manual_capture(
            raw, text_preserve_lines, manual_amount_due, manual_mode_amount_due, _AMOUNT_RE, case_ins
        )
        if amount_due:
            used_manual["amount_due"] = True
            try:
                amount_due = f"{float(amount_due.replace(',', '')):.2f}"
            except Exception:
                amount_due = None
        if not amount_due:
            totals = _find_all_amounts_after_total(text_preserve_lines)
            if totals:
                amount_due = f"{max(totals):.2f}"
            else:
                notes.append("Couldn’t confidently detect the total/amount due.")

        currency = _detect_currency(text_preserve_lines) or ""

        return {
            "ok": True,
            "text_chars": len(text_single_line),
            "text_sample": sample,
            "candidates": {
                "customer_name": customer_name or "",
                "invoice_number": invoice_number or "",
                "issue_date": issue_date or "",
                "due_date": due_date or "",
                "amount_due": amount_due or "",
                "currency": currency,
            },
            "manual_used": used_manual,
            "notes": notes,
        }

    except HTTPException:
        raise
    except Exception as e:
        print("\n=== /api/inbound/pdf/preview: UNHANDLED EXCEPTION ===", file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Server error in preview_pdf: {e.__class__.__name__}")
