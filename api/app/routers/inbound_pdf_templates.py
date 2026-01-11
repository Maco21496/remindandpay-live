# FINAL VERSION OF api/app/routers/inbound_pdf_templates.py
from __future__ import annotations
import io
import json
from typing import Dict, Any, List, Optional

import pdfplumber
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form, Body
from sqlalchemy import text
from ..database import get_db
from ..models import User
from .auth import require_user

# --- shared helpers (same geometry as extractor_line_regions) ---

def _group_words_into_lines(words: List[Dict[str, Any]], y_tol: float = 3.0) -> List[Dict[str, Any]]:
    if not words:
        return []
    words = sorted(words, key=lambda w: (w.get("top", 0.0), w.get("x0", 0.0)))
    lines: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = [words[0]]
    for w in words[1:]:
        if abs(w.get("top", 0.0) - cur[-1].get("top", 0.0)) <= y_tol:
            cur.append(w)
        else:
            lines.append(cur)
            cur = [w]
    if cur:
        lines.append(cur)

    out: List[Dict[str, Any]] = []
    for ln in lines:
        ordered = sorted(ln, key=lambda u: u.get("x0", 0.0))
        text_line = " ".join((u.get("text") or "") for u in ordered).strip()
        x0 = min(u.get("x0", 0.0) for u in ln)
        x1 = max(u.get("x1", 0.0) for u in ln)
        top = min(u.get("top", 0.0) for u in ln)
        bottom = max((u.get("bottom") if u.get("bottom") is not None else u.get("top", 0.0)) for u in ln)
        out.append({"text": text_line, "x0": x0, "x1": x1, "top": top, "bottom": bottom})
    return out

def _page_lines(page: pdfplumber.page.Page) -> List[Dict[str, Any]]:
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    return _group_words_into_lines(words, y_tol=3.0)

def _clip_by_pct(lines: List[Dict[str, Any]], page_width: float,
                 x_start_pct: float, x_end_pct: float, margin_pct: float) -> List[str]:
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

def _post(value: str, kind: str) -> str:
    import re
    kind = (kind or "").lower()
    if kind == "id":
        return re.sub(r"[^A-Za-z0-9\-\_\/\.]", "", value or "").strip()
    if kind == "amount":
        m = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2}))", value or "")
        if m:
            try:
                return f"{float(m.group(1).replace(',', '')):.2f}"
            except Exception:
                return m.group(1)
        return (value or "").strip()
    # date/text passthrough
    return (value or "").strip()

router = APIRouter(prefix="/api/inbound/lines", tags=["inbound-line-mapper"])

# ---------- Endpoints ----------

@router.post("/preview")
async def preview_lines(file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Return page-1 line list with indexes + page width/height for visual mapping.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        if not pdf.pages:
            return {"ok": True, "page_width": 0, "page_height": 0, "lines": []}
        page = pdf.pages[0]
        lines = _page_lines(page)
        out = []
        for idx, ln in enumerate(lines, start=1):
            out.append({
                "index": idx,
                "text": ln["text"],
                "x0": ln["x0"], "x1": ln["x1"],
                "top": ln["top"], "bottom": ln["bottom"],
            })
        return {"ok": True, "page_width": page.width, "page_height": page.height, "lines": out}

@router.post("/extract-one")
async def extract_one(
    file: UploadFile = File(...),
    page: int = Form(1),
    row_start: int = Form(...),
    row_end: int = Form(...),
    x_start_pct: float = Form(...),
    x_end_pct: float = Form(...),
    join_rows_mode: str = Form("space"),
    postprocess_type: str = Form("text"),
    margin_pct: float = Form(1.0),
) -> Dict[str, Any]:
    """
    Extract a single field using line-region params and return its value.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")
    pidx = max(1, int(page)) - 1
    if pidx != 0:
        # current UI works with first page only
        pass

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        if not pdf.pages:
            return {"ok": True, "value": ""}
        page = pdf.pages[pidx]
        lines = _page_lines(page)
        if not lines:
            return {"ok": True, "value": ""}
        r0 = max(1, int(row_start)) - 1
        r1 = max(1, int(row_end)) - 1
        if r0 > r1:
            r0, r1 = r1, r0
        r0 = max(0, min(r0, len(lines) - 1))
        r1 = max(0, min(r1, len(lines) - 1))
        band = _clip_by_pct(lines[r0:r1+1], page.width, x_start_pct, x_end_pct, margin_pct)
        joined = ("\n".join(band) if (join_rows_mode or "space").lower() == "newline" else " ".join(band)).strip()
        return {"ok": True, "value": _post(joined, postprocess_type)}

@router.post("/extract-template")
async def extract_template(
    file: UploadFile = File(...),
    template_json: str = Form(...)
) -> Dict[str, Any]:
    """
    Run full template (JSON) and return field->value mapping.
    """
    try:
        tpl = json.loads(template_json or "{}")
    except Exception:
        raise HTTPException(400, "Bad template_json")
    if not isinstance(tpl, dict):
        raise HTTPException(400, "Bad template_json")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        out: Dict[str, str] = {}
        fields = tpl.get("fields") or []
        for f in fields:
            key = f.get("field_key")
            if not key:
                continue
            pidx = max(1, int(f.get("page") or 1)) - 1
            if pidx < 0 or pidx >= len(pdf.pages):
                out[key] = ""
                continue
            page = pdf.pages[pidx]
            lines = _page_lines(page)
            if not lines:
                out[key] = ""
                continue
            r0 = max(1, int(f.get("row_start") or 1)) - 1
            r1 = max(1, int(f.get("row_end") or 1)) - 1
            if r0 > r1:
                r0, r1 = r1, r0
            r0 = max(0, min(r0, len(lines) - 1))
            r1 = max(0, min(r1, len(lines) - 1))

            xs = float(f.get("x_start_pct") or 0.0)
            xe = float(f.get("x_end_pct") or 100.0)
            margin = float(f.get("margin_pct") or 1.0)
            joinm = (f.get("join_rows_mode") or "space").lower()
            ptype = (f.get("postprocess", {}) or {}).get("type") or "text"

            band = _clip_by_pct(lines[r0:r1+1], page.width, xs, xe, margin)
            joined = ("\n".join(band) if joinm == "newline" else " ".join(band)).strip()
            out[key] = _post(joined, ptype)
        return {"ok": True, "fields": out}

@router.post("/save-template")
async def save_template(
    payload: Dict[str, Any] = Body(...),
    user: User = Depends(require_user),
    db = Depends(get_db),
) -> Dict[str, Any]:
    """
    Upsert template_json for current user into ic_pdf_template (PK user_id).
    """
    if "template" not in payload or not isinstance(payload["template"], dict):
        raise HTTPException(400, "template missing or invalid")
    tpl_str = json.dumps(payload["template"], separators=(",", ":"), ensure_ascii=False)

    sql = text("""
        INSERT INTO ic_pdf_template (user_id, template_json)
        VALUES (:uid, CAST(:tpl AS JSON))
        ON DUPLICATE KEY UPDATE
          template_json = VALUES(template_json),
          updated_at = CURRENT_TIMESTAMP
    """)
    db.execute(sql, {"uid": user.id, "tpl": tpl_str})
    db.commit()
    return {"ok": True, "template": payload["template"]}
