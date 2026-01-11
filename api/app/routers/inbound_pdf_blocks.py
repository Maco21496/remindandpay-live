# FINAL VERSION OF api/app/routers/inbound_pdf_blocks.py
from __future__ import annotations
import io
import json
import logging
import math
import re
from typing import List, Dict, Any, Optional, Tuple
from statistics import median
from pathlib import Path

import pdfplumber
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from .auth import require_user
from ..database import get_db

# FINAL VERSION OF PDF STORAGE CONFIG IN inbound_pdf_blocks.py
router = APIRouter(prefix="/api/inbound/blocks", tags=["pdf-invoice-imports"])
log = logging.getLogger(__name__)

# Store PDFs under the service account's home directory
PDF_STORAGE_DIR = Path.home() / "invoice_chaser_pdf_templates"


def _sanitize_template_name_for_filename(template_name: Optional[str]) -> str:
    """
    Turn a human template name into a safe filename component.
    Examples:
      "Standard invoice"  -> "standard_invoice"
      "My-Template v2"    -> "my_template_v2"
    """
    name = (template_name or "").strip()
    if not name:
        return "template"
    safe = re.sub(r"[^A-Za-z0-9]+", "_", name)
    safe = safe.strip("_")
    return safe.lower() or "template"


def _pdf_path_for_user(user_id: int, template_name: Optional[str]) -> Path:
    """
    Build the path for the stored PDF for this user + template name.
    Example: standard_invoice_user_1.pdf
    """
    safe = _sanitize_template_name_for_filename(template_name)
    return PDF_STORAGE_DIR / f"{safe}_user_{user_id}.pdf"


# -----------------------------
# pdfplumber helpers (block detection)
# -----------------------------


def _group_words_by_lines(
    words: List[Dict[str, Any]], y_tol: float = 3.0
) -> List[List[Dict[str, Any]]]:
    """Group words into line lists by similar top/vertical position."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (w.get("top", 0.0), w.get("x0", 0.0)))
    lines: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = [words[0]]
    for w in words[1:]:
        if abs(float(w.get("top", 0.0)) - float(current[-1].get("top", 0.0))) <= y_tol:
            current.append(w)
        else:
            lines.append(current)
            current = [w]
    if current:
        lines.append(current)
    return lines


def _split_line_segments(
    line_words: List[Dict[str, Any]], gap_tol: float
) -> List[List[Dict[str, Any]]]:
    """
    Split a single visual line into horizontal segments when the horizontal gap
    between consecutive words exceeds gap_tol (~ three spaces).
    """
    if not line_words:
        return []
    ws = sorted(line_words, key=lambda u: u.get("x0", 0.0))
    segments: List[List[Dict[str, Any]]] = []
    seg: List[Dict[str, Any]] = [ws[0]]
    for w in ws[1:]:
        prev = seg[-1]
        dx = float(w.get("x0", 0.0)) - float(prev.get("x1", prev.get("x0", 0.0)))
        if dx > gap_tol:
            segments.append(seg)
            seg = [w]
        else:
            seg.append(w)
    if seg:
        segments.append(seg)
    return segments


def _read_page_blocks(pdf_bytes: bytes, page_index: int = 0) -> Dict[str, Any]:
    """
    Returns { width, height, blocks: [ {id,text,bbox:{x0,y0,x1,y1}, page, line_y} ] }
    Segments each line into columns by a dynamic gap tolerance.
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not pdf.pages:
            raise HTTPException(400, "Empty PDF.")
        if page_index < 0 or page_index >= len(pdf.pages):
            raise HTTPException(400, f"Page {page_index+1} is out of range.")
        page = pdf.pages[page_index]
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []

        # Estimate space width ~ median char width
        char_widths = [
            (float(w.get("x1", 0.0)) - float(w.get("x0", 0.0)))
            / max(1, len((w.get("text") or "").strip()))
            for w in words
            if (w.get("text") or "").strip()
        ] or [4.0]
        med_char = max(1.0, float(median(char_widths)))
        gap_tol = 3.0 * med_char

        line_groups = _group_words_by_lines(words, y_tol=3.0)
        blocks: List[Dict[str, Any]] = []
        next_id = 1
        for line in line_groups:
            segments = _split_line_segments(line, gap_tol)
            # Compute a representative y for the line (median 'top' of words)
            line_y_vals = [float(u.get("top", 0.0)) for u in line]
            line_y = float(median(line_y_vals)) if line_y_vals else 0.0
            for seg in segments:
                x0 = min(float(u.get("x0", 0.0)) for u in seg)
                x1 = max(float(u.get("x1", 0.0)) for u in seg)
                top = min(float(u.get("top", 0.0)) for u in seg)
                bottom = max(
                    float(
                        (
                            u.get("bottom")
                            if u.get("bottom") is not None
                            else u.get("top", 0.0)
                        )
                    )
                    for u in seg
                )
                text_ = " ".join(
                    (u.get("text") or "").strip()
                    for u in sorted(seg, key=lambda u: u.get("x0", 0.0))
                ).strip()
                blocks.append(
                    {
                        "id": next_id,
                        "text": text_,
                        "bbox": {"x0": x0, "y0": top, "x1": x1, "y1": bottom},
                        "line_y": line_y,
                        "page": page_index + 1,
                    }
                )
                next_id += 1
        return {"width": page.width, "height": page.height, "blocks": blocks}


def _extract_text_for_blocks(
    pdf_bytes: bytes, page_index: int, block_ids: List[int]
) -> str:
    """Concatenate text of requested blocks."""
    data = _read_page_blocks(pdf_bytes, page_index)
    want = [b for b in data["blocks"] if b["id"] in set(block_ids)]
    return " ".join(b["text"] for b in want).strip()


# -----------------------------
# Filter parsing / models
# -----------------------------


def parse_filter_json_optional(raw: Optional[str]) -> Optional[dict]:
    """
    Accepts:
      - None / "" / "none" / {"type":"none"}        -> None
      - bare names: "digits_only" / "amount" / ...  -> {"type": "..."}
      - JSON object with type + params
    """
    if not raw:
        return None
    s = raw.strip()
    if (
        not s
        or s.lower() == "none"
        or s in ('{"type":"none"}', '{"type": "none"}')
    ):
        return None
    if re.fullmatch(r"[a-z_]+", s):
        return {"type": s}
    try:
        spec = json.loads(s)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid filter_json (json): {e}")
    if not isinstance(spec, dict) or "type" not in spec:
        raise HTTPException(
            status_code=400, detail="Invalid filter_json (object/type)."
        )

    t = spec.get("type")
    SIMPLE = {"digits_only", "amount", "date", "strip_parentheses"}
    if t in SIMPLE:
        return {"type": t}
    if t in {"after_token", "before_token"}:
        token = (spec.get("token") or "").strip()
        if not token:
            raise HTTPException(400, "Invalid filter_json (token missing).")
        return {"type": t, "token": token}
    if t == "between_tokens":
        left = (spec.get("left") or "").strip()
        right = (spec.get("right") or "").strip()
        if not left or not right:
            raise HTTPException(400, "Invalid filter_json (left/right missing).")
        return {"type": t, "left": left, "right": right}
    if t == "regex":
        pattern = (spec.get("pattern") or "").strip()
        if not pattern:
            raise HTTPException(400, "Invalid filter_json (pattern missing).")
        try:
            group = int(spec.get("group") or 1)
        except Exception:
            group = 1
        return {"type": "regex", "pattern": pattern, "group": group}
    if t is None or t == "none":
        return None
    raise HTTPException(400, "Invalid filter_json (unknown type).")


_AMOUNT_RE = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2}))")
_DATE_WORD = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?)|"
    r"Dec(?:ember)?"
)
_DATE_NUM = r"\d{2}/\d{2}/\d{4}"
_DATE_WORDY = rf"\d{{1,2}}\s+{_DATE_WORD}\s+\d{{4}}"
_DATE_RE = re.compile(rf"(?:{_DATE_NUM}|{_DATE_WORDY})", re.IGNORECASE)


class FilterSpec(BaseModel):
    type: str
    token: Optional[str] = None
    left: Optional[str] = None
    right: Optional[str] = None
    pattern: Optional[str] = None
    group: Optional[int] = 1


def _apply_filter(raw: str, spec: Optional[FilterSpec]) -> str:
    if not raw:
        return ""
    if not spec or not spec.type or spec.type == "none":
        return raw.strip()

    t = spec.type
    s = raw

    if t == "digits_only":
        return re.sub(r"\D+", "", s)

    if t == "amount":
        m = _AMOUNT_RE.search(s)
        if not m:
            return s.strip()
        try:
            return f"{float(m.group(1).replace(',', '')):.2f}"
        except Exception:
            return m.group(1)

    if t == "date":
        m = _DATE_RE.search(s)
        return m.group(0) if m else s.strip()

    if t == "strip_parentheses":
        return re.sub(r"\s*\([^)]*\)\s*", " ", s).strip()

    if t == "after_token" and spec.token:
        parts = s.split(spec.token, 1)
        return parts[1].strip() if len(parts) == 2 else s.strip()

    if t == "before_token" and spec.token:
        parts = s.split(spec.token, 1)
        return parts[0].strip() if len(parts) == 2 else s.strip()

    if t == "between_tokens" and spec.left and spec.right:
        try:
            left_i = s.index(spec.left) + len(spec.left)
            right_i = s.index(spec.right, left_i)
            return s[left_i:right_i].strip()
        except ValueError:
            return s.strip()

    if t == "regex" and spec.pattern:
        try:
            rx = re.compile(spec.pattern, re.IGNORECASE)
            m = rx.search(s)
            if not m:
                return s.strip()
            grp = spec.group or 1
            return (m.group(grp) or "").strip()
        except Exception:
            return s.strip()

    return s.strip()


# -----------------------------
# Schemas (template + customer map)
# -----------------------------


class Anchor(BaseModel):
    page: int = Field(1, ge=1)
    x: float
    y: float


class TemplateField(BaseModel):
    field_key: str
    trigger_text: str
    direction: str  # "right" | "below"
    anchor: Anchor
    filter: Optional[FilterSpec] = None


class CustomerMap(BaseModel):
    by: str  # "name" | "email"
    trigger_text: str
    direction: str  # "right" | "below"
    anchor: Anchor
    filter: Optional[FilterSpec] = None


class TemplateModel(BaseModel):
    template_id: str
    page: int = 1  # kept for backwards compatibility with UI "page" input
    fields: List[TemplateField]
    customer_map: Optional[CustomerMap] = None


# -----------------------------
# Shared helpers
# -----------------------------


def _pyd_validate(cls, data: dict):
    if hasattr(cls, "model_validate"):
        return getattr(cls, "model_validate")(data)
    if hasattr(cls, "parse_obj"):
        return getattr(cls, "parse_obj")(data)
    return cls(**data)


def _pyd_validate_json(cls, data_json: str):
    if hasattr(cls, "model_validate_json"):
        return getattr(cls, "model_validate_json")(data_json)
    if hasattr(cls, "parse_raw"):
        return getattr(cls, "parse_raw")(data_json)
    return cls(**json.loads(data_json))


def _pyd_dump(model_obj: Any):
    if model_obj is None:
        return None
    if hasattr(model_obj, "model_dump"):
        return getattr(model_obj, "model_dump")()
    if hasattr(model_obj, "dict"):
        return getattr(model_obj, "dict")()
    try:
        return dict(model_obj)
    except Exception:
        return str(model_obj)


def _get_user_id_from_require_user(user_obj: Any) -> int:
    uid = getattr(user_obj, "id", None)
    if uid is None and isinstance(user_obj, dict):
        uid = user_obj.get("id")
    try:
        uid = int(uid) if uid is not None else None
    except Exception:
        uid = None
    if uid is None:
        raise HTTPException(status_code=401, detail="Current user id not available.")
    return uid


def _center_of(bbox: Dict[str, float]) -> Tuple[float, float]:
    return (
        (float(bbox["x0"]) + float(bbox["x1"])) / 2.0,
        (float(bbox["y0"]) + float(bbox["y1"])) / 2.0,
    )


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _read_page_blocks_cached(pdf_bytes: bytes, page_index: int) -> Dict[str, Any]:
    return _read_page_blocks(pdf_bytes, page_index)


def _find_best_trigger_block(
    blocks: List[Dict[str, Any]],
    trigger_text: str,
    anchor_xy: Tuple[float, float],
) -> Optional[Dict[str, Any]]:
    """Among blocks whose text contains trigger_text (case-insensitive),
    pick the one whose center is closest to anchor_xy."""
    needle = (trigger_text or "").strip()
    if not needle:
        return None
    needle_lower = needle.lower()
    cands = [
        b for b in blocks if needle_lower in (b.get("text") or "").lower()
    ]
    if not cands:
        return None
    best = min(cands, key=lambda b: _dist(_center_of(b["bbox"]), anchor_xy))
    return best


def _extract_by_trigger_and_direction(
    blocks: List[Dict[str, Any]],
    width: float,
    height: float,
    trigger_block: Dict[str, Any],
    trigger_text: str,
    direction: str,
) -> str:
    """
    direction:
      - "right": take substring after trigger in the same block if present,
                 otherwise take first block on same visual line whose x0 > trigger_block.x1
      - "below": take first block on next line (smallest y0 greater than trigger_block.y1)
    """
    t = (trigger_text or "").strip()
    tb = trigger_block
    tb_text = tb.get("text") or ""
    tb_bbox = tb.get("bbox") or {}
    tb_x1 = float(tb_bbox.get("x1", 0.0))
    tb_y1 = float(tb_bbox.get("y1", 0.0))
    tb_line_y = float(tb.get("line_y", tb_bbox.get("y0", 0.0)))

    if direction == "right":
        idx = tb_text.lower().find(t.lower())
        if idx >= 0:
            after = tb_text[idx + len(t):].strip()
            if after:
                return after
        same_line = [
            b
            for b in blocks
            if abs(float(b.get("line_y", 0.0)) - tb_line_y) <= 2.5
        ]
        right_blocks = [
            b
            for b in same_line
            if float(b["bbox"]["x0"]) >= tb_x1 - 0.5
        ]
        right_blocks.sort(key=lambda b: float(b["bbox"]["x0"]))
        if right_blocks:
            return (right_blocks[0].get("text") or "").strip()
        return ""

    below_blocks = [
        b for b in blocks if float(b["bbox"]["y0"]) > tb_y1 + 1.0
    ]
    tb_cx = (float(tb_bbox.get("x0", 0.0)) + tb_x1) / 2.0
    below_blocks.sort(
        key=lambda b: (
            float(b["bbox"]["y0"]) - tb_y1,
            abs(
                (
                    (float(b["bbox"]["x0"]) + float(b["bbox"]["x1"])) / 2.0
                )
                - tb_cx
            ),
        )
    )
    if below_blocks:
        return (below_blocks[0].get("text") or "").strip()
    return ""


# -----------------------------
# Endpoints
# -----------------------------


@router.post("/preview", dependencies=[Depends(require_user)])
async def preview_blocks(
    file: UploadFile = File(...),
    page: int = Form(1),
) -> Dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    raw = await file.read()
    try:
        data = _read_page_blocks(raw, page_index=max(0, page - 1))
        return {"ok": True, "page": page, **data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to read PDF: {e.__class__.__name__}")


@router.post("/preview-value", dependencies=[Depends(require_user)])
async def preview_value(
    file: UploadFile = File(...),
    page: int = Form(1),
    block_ids: Optional[str] = Form(None),
    ids: Optional[str] = Form(None),
    filter_json: Optional[str] = Form(None),
    filter: Optional[str] = Form(None),
) -> Dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    ids_src = (
        block_ids if (block_ids is not None and block_ids.strip() != "") else ids
    )
    if not ids_src:
        raise HTTPException(400, "block_ids must be CSV of integers.")
    try:
        id_list = [int(x) for x in ids_src.split(",") if x.strip()]
        if not id_list:
            raise ValueError("empty")
    except Exception:
        raise HTTPException(400, "block_ids must be CSV of integers.")

    parsed: Optional[dict] = None
    if filter_json and filter_json.strip():
        parsed = parse_filter_json_optional(filter_json.strip())
    elif filter and filter.strip():
        parsed = parse_filter_json_optional(filter.strip())

    spec: Optional[FilterSpec] = None
    if parsed is not None:
        try:
            spec = _pyd_validate(FilterSpec, parsed)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid filter_json (validate): {e}",
            )

    raw = await file.read()
    text_val = _extract_text_for_blocks(
        raw, page_index=max(0, page - 1), block_ids=id_list
    )
    log.debug(
        "/preview-value ok: page=%s ids=%s filter=%s",
        page,
        id_list,
        _pyd_dump(spec),
    )
    val = _apply_filter(text_val, spec)
    return {"ok": True, "value": val, "raw": text_val}


@router.post("/preview-by-trigger", dependencies=[Depends(require_user)])
async def preview_by_trigger(
    file: UploadFile = File(...),
    page: int = Form(1),
    anchor_block_id: int = Form(...),
    trigger_text: str = Form(...),
    direction: str = Form(...),
    filter_json: Optional[str] = Form(None),
    filter: Optional[str] = Form(None),
) -> Dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    direction = (direction or "").lower().strip()
    if direction not in {"right", "below"}:
        raise HTTPException(400, "direction must be 'right' or 'below'.")

    parsed: Optional[dict] = None
    if filter_json and filter_json.strip():
        parsed = parse_filter_json_optional(filter_json.strip())
    elif filter and filter.strip():
        parsed = parse_filter_json_optional(filter.strip())
    spec: Optional[FilterSpec] = None
    if parsed is not None:
        try:
            spec = _pyd_validate(FilterSpec, parsed)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid filter_json (validate): {e}",
            )

    raw = await file.read()
    page_index = max(0, page - 1)
    page_data = _read_page_blocks_cached(raw, page_index)
    blocks = [b for b in page_data["blocks"] if int(b["page"]) == page]
    width, height = float(page_data["width"]), float(page_data["height"])

    anchor = next(
        (b for b in blocks if int(b["id"]) == int(anchor_block_id)), None
    )
    if not anchor:
        raise HTTPException(400, "anchor_block_id not found on this page.")

    anchor_xy = _center_of(anchor["bbox"])
    trig = _find_best_trigger_block(blocks, trigger_text, anchor_xy)
    if not trig:
        return {
            "ok": True,
            "value": "",
            "raw": "",
            "reason": "trigger_not_found",
        }

    raw_val = _extract_by_trigger_and_direction(
        blocks, width, height, trig, trigger_text, direction
    )
    val = _apply_filter(raw_val, spec)
    return {"ok": True, "value": val, "raw": raw_val}

# FINAL VERSION OF _extract_fields_from_pdf_bytes() IN inbound_pdf_blocks.py
def _extract_fields_from_pdf_bytes(pdf_bytes: bytes, tpl: TemplateModel) -> Dict[str, str]:
    """
    Run the template extraction against the given PDF bytes and return
    a dict of field_key -> extracted value, plus _customer_lookup_value
    for the customer_map (if present).

    IMPORTANT:
    - We ignore any TemplateField with field_key == "customer_map" in tpl.fields.
      Customer mapping is driven solely by tpl.customer_map.
    """
    out: Dict[str, str] = {}

    # Invoice fields (trigger-based). Skip any legacy "customer_map" entry.
    for f in tpl.fields:
        if f.field_key == "customer_map":
            continue

        page_index = max(
            0,
            (f.anchor.page if f.anchor and f.anchor.page else tpl.page) - 1,
        )
        data = _read_page_blocks_cached(pdf_bytes, page_index)
        blocks = [
            b for b in data["blocks"] if int(b["page"]) == (page_index + 1)
        ]
        width, height = float(data["width"]), float(data["height"])

        anchor_xy = (float(f.anchor.x), float(f.anchor.y))
        trig = _find_best_trigger_block(
            blocks, f.trigger_text, anchor_xy
        )
        if not trig:
            out[f.field_key] = ""
            continue

        raw_val = _extract_by_trigger_and_direction(
            blocks, width, height, trig, f.trigger_text, f.direction.lower()
        )
        out[f.field_key] = _apply_filter(raw_val, f.filter)

    # Customer_map (trigger-based) – canonical source of customer lookup value.
    if tpl.customer_map:
        cm = tpl.customer_map
        try:
            page_index = max(
                0,
                (
                    cm.anchor.page
                    if cm.anchor and cm.anchor.page
                    else tpl.page
                )
                - 1,
            )
            data = _read_page_blocks_cached(pdf_bytes, page_index)
            blocks = [
                b
                for b in data["blocks"]
                if int(b["page"]) == (page_index + 1)
            ]
            width, height = float(data["width"]), float(data["height"])
            anchor_xy = (float(cm.anchor.x), float(cm.anchor.y))
            trig = _find_best_trigger_block(
                blocks, cm.trigger_text, anchor_xy
            )
            if trig:
                raw_val = _extract_by_trigger_and_direction(
                    blocks,
                    width,
                    height,
                    trig,
                    cm.trigger_text,
                    cm.direction.lower(),
                )
                out["_customer_lookup_value"] = _apply_filter(
                    raw_val, cm.filter
                )
            else:
                out["_customer_lookup_value"] = ""
        except Exception:
            out["_customer_lookup_value"] = ""

    return out



@router.post("/extract-template", dependencies=[Depends(require_user)])
async def extract_with_template(
    file: UploadFile = File(...),
    template_json: str = Form(...),
) -> Dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    try:
        tpl = _pyd_validate_json(TemplateModel, template_json)
    except Exception as e:
        raise HTTPException(400, detail=f"Invalid template_json: {e}")

    raw = await file.read()
    out = _extract_fields_from_pdf_bytes(raw, tpl)
    return {"ok": True, "fields": out, "template": tpl.model_dump()}

    # fields (trigger-based)
    for f in tpl.fields:
        page_index = max(
            0,
            (f.anchor.page if f.anchor and f.anchor.page else tpl.page) - 1,
        )
        data = _read_page_blocks_cached(raw, page_index)
        blocks = [
            b for b in data["blocks"] if int(b["page"]) == (page_index + 1)
        ]
        width, height = float(data["width"]), float(data["height"])

        anchor_xy = (float(f.anchor.x), float(f.anchor.y))
        trig = _find_best_trigger_block(
            blocks, f.trigger_text, anchor_xy
        )
        if not trig:
            out[f.field_key] = ""
            continue

        raw_val = _extract_by_trigger_and_direction(
            blocks, width, height, trig, f.trigger_text, f.direction.lower()
        )
        out[f.field_key] = _apply_filter(raw_val, f.filter)

    # customer_map (trigger-based)
    if tpl.customer_map:
        cm = tpl.customer_map
        try:
            page_index = max(
                0,
                (
                    cm.anchor.page
                    if cm.anchor and cm.anchor.page
                    else tpl.page
                )
                - 1,
            )
            data = _read_page_blocks_cached(raw, page_index)
            blocks = [
                b
                for b in data["blocks"]
                if int(b["page"]) == (page_index + 1)
            ]
            width, height = float(data["width"]), float(data["height"])
            anchor_xy = (float(cm.anchor.x), float(cm.anchor.y))
            trig = _find_best_trigger_block(
                blocks, cm.trigger_text, anchor_xy
            )
            if trig:
                raw_val = _extract_by_trigger_and_direction(
                    blocks,
                    width,
                    height,
                    trig,
                    cm.trigger_text,
                    cm.direction.lower(),
                )
                out["_customer_lookup_value"] = _apply_filter(
                    raw_val, cm.filter
                )
            else:
                out["_customer_lookup_value"] = ""
        except Exception:
            out["_customer_lookup_value"] = ""

    return {"ok": True, "fields": out, "template": tpl.model_dump()}


# -----------------------------
# Persistence: load/save template JSON for current user
# -----------------------------

@router.get("/templates", dependencies=[Depends(require_user)])
def list_templates(
    current_user: Any = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Return all templates for the current user so the UI can populate a selector.
    Each entry includes basic metadata and whether a stored PDF file exists.
    """
    user_id = _get_user_id_from_require_user(current_user)

    rows = db.execute(
        text(
            """
            SELECT template_name, created_at, updated_at
            FROM ic_pdf_template
            WHERE user_id = :uid
            ORDER BY updated_at DESC, created_at DESC
            """
        ),
        {"uid": user_id},
    ).fetchall()

    templates: List[Dict[str, Any]] = []
    for row in rows:
        name, created_at, updated_at = row
        if not name:
            continue
        pdf_path = _pdf_path_for_user(user_id, name)
        templates.append(
            {
                "template_name": name,
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "pdf_exists": pdf_path.exists(),
            }
        )

    return {"ok": True, "templates": templates}


@router.get("/load-template", dependencies=[Depends(require_user)])
def load_template(
    current_user: Any = Depends(require_user),
    db: Session = Depends(get_db),
    template_name: Optional[str] = None,
):
    """
    Load a single template for the current user.

    - If template_name is provided, load that template (most recently updated row).
    - If not provided, load the most recently updated template for this user.
    Additionally, if a stored PDF exists for this template, we run the
    extraction engine once and return sample_fields so the UI can display
    example values (e.g. "1. Invoice number | 790").
    """
    user_id = _get_user_id_from_require_user(current_user)

    params: Dict[str, Any] = {"uid": user_id}
    if template_name:
        cleaned = template_name.strip()
        params["name"] = cleaned
        row = db.execute(
            text(
                """
                SELECT template_name, template_json
                FROM ic_pdf_template
                WHERE user_id = :uid AND template_name = :name
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """
            ),
            params,
        ).fetchone()
    else:
        row = db.execute(
            text(
                """
                SELECT template_name, template_json
                FROM ic_pdf_template
                WHERE user_id = :uid
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """
            ),
            params,
        ).fetchone()

    template_name_out: Optional[str] = None
    tpl: Any = {}
    pdf_exists = False
    sample_fields: Dict[str, Any] = {}

    if row:
        template_name_out, tpl = row
        if isinstance(tpl, str):
            try:
                tpl = json.loads(tpl)
            except Exception:
                tpl = {}
        if template_name_out:
            pdf_path = _pdf_path_for_user(user_id, template_name_out)
            pdf_exists = pdf_path.exists()

            # If we have both a template and a stored PDF, run a single
            # extraction pass to produce example values for the UI.
            if pdf_exists:
                try:
                    raw = pdf_path.read_bytes()
                    tpl_model = _pyd_validate(TemplateModel, tpl)
                    sample_fields = _extract_fields_from_pdf_bytes(raw, tpl_model)
                except Exception as e:
                    log.error(
                        "load_template: failed to extract sample fields for user %s template %s: %s",
                        user_id,
                        template_name_out,
                        e,
                    )
                    sample_fields = {}
    else:
        tpl = {}

    return {
        "ok": True,
        "template_name": template_name_out,
        "template_json": tpl,
        "pdf_exists": pdf_exists,
        "sample_fields": sample_fields,
    }


@router.post("/save-template", dependencies=[Depends(require_user)])
def save_template(
    template_json: str = Form(...),
    template_name: str = Form(""),
    current_user: Any = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Save or update a template for this user.

    Key is (user_id, template_name):
      - If a row for that (user, name) exists, we update its JSON + updated_at.
      - Otherwise we insert a new row.
    Each template keeps its own PDF file; we no longer rename PDFs when the name changes.
    """
    user_id = _get_user_id_from_require_user(current_user)

    # Validate template_json against TemplateModel
    try:
        _ = _pyd_validate_json(TemplateModel, template_json)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid template_json: {e}")

    cleaned_name = (template_name or "").strip()
    if not cleaned_name:
        raise HTTPException(status_code=400, detail="template_name is required.")

    # First try to update an existing row for this (user_id, template_name)
    result = db.execute(
        text(
            """
            UPDATE ic_pdf_template
            SET template_json = CAST(:tpl AS JSON),
                updated_at    = NOW()
            WHERE user_id = :uid
              AND template_name = :name
            """
        ),
        {"uid": user_id, "name": cleaned_name, "tpl": template_json},
    )

    # If nothing was updated, insert a new row
    if result.rowcount == 0:
        db.execute(
            text(
                """
                INSERT INTO ic_pdf_template (user_id, template_name, template_json, created_at, updated_at)
                VALUES (:uid, :name, CAST(:tpl AS JSON), NOW(), NOW())
                """
            ),
            {"uid": user_id, "name": cleaned_name, "tpl": template_json},
        )

    db.commit()
    return {"ok": True}


@router.post("/upload-pdf", dependencies=[Depends(require_user)])
async def upload_pdf(
    file: UploadFile = File(...),
    template_name: str = Form(""),
    current_user: Any = Depends(require_user),
):
    """
    Store the uploaded PDF for this user + template_name.
    Filename on disk: <sanitised_template_name>_user_<user_id>.pdf
    """
    user_id = _get_user_id_from_require_user(current_user)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    cleaned_name = (template_name or "").strip()
    if not cleaned_name:
        raise HTTPException(status_code=400, detail="template_name is required for PDF upload.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty PDF file.")

    # Ensure storage directory exists
    try:
        PDF_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.error("Failed to create PDF storage dir %s: %s", PDF_STORAGE_DIR, e)
        raise HTTPException(
            status_code=500,
            detail="Failed to create PDF storage directory.",
        )

    pdf_path = _pdf_path_for_user(user_id, cleaned_name)

    try:
        pdf_path.write_bytes(data)
    except Exception as e:
        log.error("Failed to write stored PDF for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Failed to store PDF.")

    return {
        "ok": True,
        "filename": file.filename,
        "size": len(data),
        "stored_path": str(pdf_path),
    }


@router.get("/download-pdf", dependencies=[Depends(require_user)])
def download_pdf(
    current_user: Any = Depends(require_user),
    db: Session = Depends(get_db),
    template_name: str = "",
):
    """
    Download the stored PDF for the current user + template_name.
    """
    user_id = _get_user_id_from_require_user(current_user)

    cleaned_name = (template_name or "").strip()
    if not cleaned_name:
        raise HTTPException(status_code=400, detail="template_name is required.")

    # Only serve if there is a template row for this (user, name)
    row = db.execute(
        text(
            """
            SELECT 1
            FROM ic_pdf_template
            WHERE user_id = :uid AND template_name = :name
            LIMIT 1
            """
        ),
        {"uid": user_id, "name": cleaned_name},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found for this user.")

    pdf_path = _pdf_path_for_user(user_id, cleaned_name)

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="No stored PDF for this template.")

    try:
        data = pdf_path.read_bytes()
    except Exception as e:
        log.error("Failed to read stored PDF for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Failed to read stored PDF.")

    return Response(content=data, media_type="application/pdf")


def _debug_try_write(dir_path: Path, basename: str) -> Dict[str, Any]:
    """
    Try mkdir + write a small text file in dir_path.
    Returns a dict with ok flags and any exception messages.
    """
    info: Dict[str, Any] = {
        "dir": str(dir_path),
        "file": str(dir_path / basename),
    }

    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        info["mkdir_ok"] = True
    except Exception as e:
        info["mkdir_ok"] = False
        info["mkdir_error"] = f"{e.__class__.__name__}: {e}"
        return info

    test_path = dir_path / basename
    try:
        test_path.write_text("invoice_chaser API write test\n", encoding="utf-8")
        info["write_ok"] = True
    except Exception as e:
        info["write_ok"] = False
        info["write_error"] = f"{e.__class__.__name__}: {e}"

    return info


# FINAL VERSION OF debug-test-write endpoint in inbound_pdf_blocks.py
@router.get("/debug-test-write", dependencies=[Depends(require_user)])
def debug_test_write(
    current_user: Any = Depends(require_user),
):
    """
    Debug helper:
      - tries to mkdir + write a small file into 'reports' and PDF_STORAGE_DIR
      - returns home dir / cwd so we can see what profile the API is running under
    """
    user_id = _get_user_id_from_require_user(current_user)

    reports_dir = Path(
        r"C:\Users\Administrator\Documents\invoice_chaser_app\invoice_chaser\reports"
    )

    def _test_dir(dir_path: Path) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "dir": str(dir_path),
        }

        # mkdir test
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            info["mkdir_ok"] = True
        except Exception as e:
            info["mkdir_ok"] = False
            info["write_ok"] = False
            info["write_error"] = f"mkdir: {e.__class__.__name__}: {e}"
            return info

        # write file test
        test_file = dir_path / f"api_debug_user_{user_id}.txt"
        info["file"] = str(test_file)
        try:
            test_file.write_text("debug from API process\n", encoding="utf-8")
            info["write_ok"] = True
        except Exception as e:
            info["write_ok"] = False
            info["write_error"] = f"{e.__class__.__name__}: {e}"

        return info

    return {
        "ok": True,
        "user_id": user_id,
        "home_dir": str(Path.home()),
        "cwd": str(Path.cwd()),
        "reports_dir": str(reports_dir),
        "pdf_storage_dir": str(PDF_STORAGE_DIR),
        "reports_test": _test_dir(reports_dir),
        "pdf_storage_test": _test_dir(PDF_STORAGE_DIR),
    }