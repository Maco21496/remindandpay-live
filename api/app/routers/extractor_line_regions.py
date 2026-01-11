# FINAL VERSION OF api/app/routers/extractor_line_regions.py
from __future__ import annotations
import io
import re
import json
from typing import List, Dict, Any, Optional

import pdfplumber
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Depends
from .auth import require_user

router = APIRouter(prefix="/api/inbound/lines", tags=["inbound-pdf-lines"])

# -----------------------------
# Text + geometry utilities
# -----------------------------

_WS_RE = re.compile(r"[ \t]+")

def _clean(s: str) -> str:
    s = s.replace("\r", "")
    s = s.replace("£", " GBP ").replace("(E)", " GBP ")
    s = _WS_RE.sub(" ", s)
    return s.strip()

def _group_words_into_lines(words: List[Dict[str, Any]], y_tol: float = 3.0) -> List[Dict[str, Any]]:
    """
    Group words into visual lines by proximity of 'top'. Each line:
      { 'text': str, 'x0': float, 'x1': float, 'top': float, 'bottom': float }
    Coordinates are in PDF points.
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
        text = " ".join((u.get("text") or "") for u in ordered)
        x0 = min(u.get("x0", 0.0) for u in ln)
        x1 = max(u.get("x1", 0.0) for u in ln)
        top = min(u.get("top", 0.0) for u in ln)
        bottom = max((u.get("bottom") if u.get("bottom") is not None else u.get("top", 0.0)) for u in ln)
        out.append({"text": _clean(text), "x0": x0, "x1": x1, "top": top, "bottom": bottom})
    return out

def _page_lines(page: pdfplumber.page.Page) -> List[Dict[str, Any]]:
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    return _group_words_into_lines(words, y_tol=3.0)

def extract_document_lines(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Returns list of pages, each page:
      { 'width': float, 'height': float, 'lines': [ ... as in _group_words_into_lines ... ] }
    """
    out: List[Dict[str, Any]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            out.append({
                "width": page.width,
                "height": page.height,
                "lines": _page_lines(page),
            })
    return out

def _clip_by_pct(
    lines: List[Dict[str, Any]],
    page_width: float,
    x_start_pct: float,
    x_end_pct: float,
    margin_pct: float
) -> List[str]:
    """
    From a page's lines, return texts whose horizontal span overlaps the [x_start..x_end] band (with margin).
    x_start_pct, x_end_pct, margin_pct are percentages of page_width (0..100).
    """
    x0 = max(0.0, (x_start_pct / 100.0) * page_width)
    x1 = min(page_width, (x_end_pct / 100.0) * page_width)
    if margin_pct and margin_pct > 0:
        pad = (margin_pct / 100.0) * page_width
        x0 = max(0.0, x0 - pad)
        x1 = min(page_width, x1 + pad)

    out: List[str] = []
    for ln in lines:
        lx0, lx1 = ln["x0"], ln["x1"]
        inter = max(0.0, min(lx1, x1) - max(lx0, x0))
        width = min((lx1 - lx0), (x1 - x0)) if (lx1 > lx0 and x1 > x0) else 0.0
        if width > 0 and inter / width >= 0.25:
            out.append(ln["text"])
    return out

def _postprocess(value: str, pp: Optional[Dict[str, Any]]) -> str:
    if not value:
        return value
    pp = pp or {}
    t = (pp.get("type") or "").lower()

    if t == "id":
        v = re.sub(r"[^A-Za-z0-9\-\_\/\.]", "", value)
        return v.strip()

    if t == "date":
        # Keep exact token; downstream parsing can handle format specifics.
        return value.strip()

    if t == "amount":
        m = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2}))", value)
        if m:
            try:
                return f"{float(m.group(1).replace(',', '')):.2f}"
            except Exception:
                return m.group(1)
        return value.strip()

    # default: text
    return _WS_RE.sub(" ", value).strip()

def extract_fields_from_template(pdf_bytes: bytes, template: Dict[str, Any]) -> Dict[str, str]:
    """
    template:
      {
        "template_id": "...",
        "fields": [
          {
            "field_key": "customer_name",
            "page": 1,
            "row_start": 6,
            "row_end": 8,
            "x_start_pct": 45.0,
            "x_end_pct": 95.0,
            "join_rows_mode": "space",    # "space" | "newline"
            "postprocess": {"type":"text"},   # "text"|"id"|"date"|"amount"
            "margin_pct": 1.0
          },
          ...
        ]
      }
    """
    pages = extract_document_lines(pdf_bytes)
    out: Dict[str, str] = {}
    fields = template.get("fields") or []
    for f in fields:
        key = f.get("field_key")
        if not key:
            continue

        page_idx = max(1, int(f.get("page") or 1)) - 1
        if page_idx < 0 or page_idx >= len(pages):
            out[key] = ""
            continue

        page = pages[page_idx]
        page_lines = page["lines"]
        width = float(page["width"])

        r0 = max(1, int(f.get("row_start") or 1)) - 1
        r1 = max(1, int(f.get("row_end") or 1)) - 1
        if r0 > r1:
            r0, r1 = r1, r0

        if not page_lines:
            out[key] = ""
            continue
        r0 = max(0, min(r0, len(page_lines) - 1))
        r1 = max(0, min(r1, len(page_lines) - 1))

        sublines = page_lines[r0:r1+1]
        x0p = float(f.get("x_start_pct") or 0.0)
        x1p = float(f.get("x_end_pct") or 100.0)
        margin = float(f.get("margin_pct") or 0.0)

        clipped_texts: List[str] = []
        for ln in sublines:
            clipped = _clip_by_pct([ln], width, x0p, x1p, margin)
            if clipped:
                clipped_texts.append(clipped[0])

        join_mode = (f.get("join_rows_mode") or "space").lower()
        joined = ("\n".join(clipped_texts) if join_mode == "newline" else " ".join(clipped_texts)).strip()

        out[key] = _postprocess(joined, f.get("postprocess"))
    return out

# -----------------------------
# Endpoints
# -----------------------------

# FINAL VERSION OF /api/inbound/lines/preview (returns per-page lines with coords)
@router.post("/preview")
async def preview_lines(
    file: UploadFile = File(...),
    user = Depends(require_user),
) -> Dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")

    pages = extract_document_lines(raw)

    # Add 1-based line indices for UI; don’t truncate texts, UI can decide what to show
    resp_pages: List[Dict[str, Any]] = []
    for p in pages:
        lines = p["lines"]
        resp_pages.append({
            "width": p["width"],
            "height": p["height"],
            "lines": [
                {
                    "index": i + 1,
                    "text": ln["text"],
                    "x0": ln["x0"],
                    "x1": ln["x1"],
                    "top": ln["top"],
                    "bottom": ln["bottom"],
                }
                for i, ln in enumerate(lines)
            ],
        })

    return {"ok": True, "pages": resp_pages}

# FINAL VERSION OF /api/inbound/lines/extract (multipart: file + template_json)
@router.post("/extract")
async def extract_with_template(
    file: UploadFile = File(...),
    template_json: str = Form(...),
    user = Depends(require_user),
) -> Dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")

    try:
        template = json.loads(template_json)
        if not isinstance(template, dict):
            raise ValueError("template_json must be an object")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid template_json: {e}")

    fields = extract_fields_from_template(raw, template)
    return {"ok": True, "fields": fields}
