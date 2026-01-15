# FINAL VERSION OF api/app/routers/inbound_settings_app.py
from __future__ import annotations

import json
import re
import base64
from html import unescape
from html.parser import HTMLParser
from typing import Optional, List, Dict, Any
from decimal import Decimal, InvalidOperation
from datetime import datetime

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text as sqltext, func, bindparam, text

from ..database import get_db
from .auth import require_user  # used for the toggle endpoints
from ..models import Invoice, Customer
from ..calculate_due_date import compute_due_date

from .inbound_pdf import _extract_text
from .inbound_pdf_blocks import (
    TemplateModel as BlockTemplateModel,
    FilterSpec,
    _extract_text_for_blocks,
    _apply_filter,
    _pyd_validate_json,
    _read_page_blocks_cached,
    _find_best_trigger_block,
    _extract_by_trigger_and_direction,
)

router = APIRouter(prefix="/api/postmark", tags=["postmark-inbound"])

LOCAL_TOKEN_RE = re.compile(r"^inb_([a-f0-9]{16,40})(?:\+[^@]+)?$", re.IGNORECASE)

def _extract_token_from_rcpt(addr: str) -> Optional[str]:
    try:
        local = (addr or "").split("@", 1)[0]
        m = LOCAL_TOKEN_RE.match(local)
        if m:
            return m.group(1).lower()
    except Exception:
        pass
    return None

def _collect_recipient_addresses(data: dict) -> List[str]:
    addrs: List[str] = []
    orig = data.get("OriginalRecipient")
    if isinstance(orig, str) and orig.strip():
        addrs.append(orig.strip())
    to_field = data.get("To")
    if isinstance(to_field, str) and to_field.strip():
        for part in to_field.split(","):
            part = part.strip()
            if part:
                addrs.append(part)
    for key in ("ToFull", "CcFull", "BccFull"):
        full_list = data.get(key) or []
        if not isinstance(full_list, list):
            continue
        for item in full_list:
            if not isinstance(item, dict):
                continue
            email = item.get("Email")
            if isinstance(email, str) and email.strip():
                addrs.append(email.strip())

    seen = set()
    uniq: List[str] = []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq

def _first_pdf_attachment(data: dict) -> tuple[Optional[bytes], Optional[str]]:
    atts = data.get("Attachments") or []
    if not isinstance(atts, list):
        return None, None
    for att in atts:
        if not isinstance(att, dict):
            continue
        ctype = (att.get("ContentType") or "").lower()
        name = att.get("Name") or ""
        if "pdf" not in ctype and not name.lower().endswith(".pdf"):
            continue
        content_b64 = att.get("Content") or ""
        if not content_b64:
            continue
        try:
            pdf_bytes = base64.b64decode(content_b64, validate=False)
            if pdf_bytes:
                return pdf_bytes, (name or None)
        except Exception:
            continue
    return None, None

def _extract_pdf_attachments(data: dict) -> List[tuple[bytes, Optional[str]]]:
    """
    Return ALL PDF attachments as a list of (pdf_bytes, filename_or_None).

    This is the multi-attachment version of _first_pdf_attachment: it uses the
    same filters (ContentType or .pdf extension) but does not stop at the first.
    """
    out: List[tuple[bytes, Optional[str]]] = []
    atts = data.get("Attachments") or []
    if not isinstance(atts, list):
        return out

    for att in atts:
        if not isinstance(att, dict):
            continue
        ctype = (att.get("ContentType") or "").lower()
        name = att.get("Name") or ""
        if "pdf" not in ctype and not name.lower().endswith(".pdf"):
            continue

        content_b64 = att.get("Content") or ""
        if not content_b64:
            continue

        try:
            pdf_bytes = base64.b64decode(content_b64, validate=False)
        except Exception:
            continue

        if not pdf_bytes:
            continue

        out.append((pdf_bytes, name or None))

    return out


def _html_to_text(html_body: str) -> str:
    if not html_body:
        return ""
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\\1>", "", html_body)
    cleaned = re.sub(r"(?i)<br\\s*/?>", "\n", cleaned)
    cleaned = re.sub(
        r"(?i)</(p|div|tr|li|h\\d|table|section|article|header|footer|tbody|thead|tfoot)>",
        "\n",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)<(p|div|tr|li|h\\d|table|section|article|header|footer|tbody|thead|tfoot)[^>]*>",
        "\n",
        cleaned,
    )
    cleaned = re.sub(r"(?s)<[^>]+>", "", cleaned)
    cleaned = unescape(cleaned)
    cleaned = cleaned.replace("\\r", "")
    cleaned = re.sub(r"\\n{3,}", "\\n\\n", cleaned)
    return cleaned.strip()


class _HtmlNode:
    def __init__(self, tag: str, attrs: Dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list["_HtmlNode"] = []
        self.text_parts: list[str] = []

    def text_content(self) -> str:
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text_content())
        return " ".join(p.strip() for p in parts if p.strip())


class _HtmlTreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root = _HtmlNode("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        attrs_dict = {key: value for key, value in attrs if key}
        node = _HtmlNode(tag.lower(), attrs_dict)
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self.stack[-1].text_parts.append(data)


def _find_body_node(node: _HtmlNode) -> _HtmlNode:
    if node.tag == "body":
        return node
    for child in node.children:
        found = _find_body_node(child)
        if found.tag == "body":
            return found
    return node


def _extract_value_from_dom(html_body: str, spec: Dict[str, Any]) -> str:
    if not html_body:
        return ""
    try:
        parser = _HtmlTreeBuilder()
        parser.feed(html_body)
        parser.close()
    except Exception:
        return ""

    path = spec.get("path")
    if not isinstance(path, list):
        return ""

    node = _find_body_node(parser.root)
    for step in path:
        if not isinstance(step, dict):
            return ""
        index = step.get("index")
        tag = step.get("tag")
        if not isinstance(index, int):
            return ""
        if index < 0 or index >= len(node.children):
            return ""
        node = node.children[index]
        if tag and node.tag != tag:
            return ""

    attr = spec.get("attr") or "text"
    if attr == "text":
        return (node.text_content() or "").strip()
    return (node.attrs.get(attr) or "").strip()


def _extract_fields_from_html(
    text: str,
    html_body: str,
    template_json: Dict[str, Any],
) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    field_map = template_json.get("fields") if isinstance(template_json, dict) else None
    if not isinstance(field_map, dict):
        return fields

    for key, spec in field_map.items():
        if not isinstance(spec, dict):
            continue
        filter_spec = None
        raw_filter = spec.get("filter")
        if isinstance(raw_filter, dict):
            try:
                filter_spec = FilterSpec(**raw_filter)
            except Exception:
                filter_spec = None
        elif raw_filter is not None:
            filter_spec = raw_filter
        if spec.get("type") == "dom" and isinstance(spec.get("path"), list):
            value = _extract_value_from_dom(html_body or "", spec)
            if value:
                fields[key] = _apply_filter(value, filter_spec) if filter_spec else value
                continue

        pattern = spec.get("regex")
        if not pattern:
            continue
        group = spec.get("group", 1)
        try:
            rx = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        except re.error:
            continue
        match = rx.search(text)
        if not match:
            fields[key] = ""
            continue
        try:
            value = match.group(int(group))
        except Exception:
            value = match.group(1)
        raw_value = (value or "").strip()
        fields[key] = _apply_filter(raw_value, filter_spec) if filter_spec else raw_value
    return fields


def _load_html_template_for_user(
    db: Session,
    user_id: int,
    template_name: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not template_name:
        return None

    row = db.execute(
        sqltext(
            """
            SELECT html_template_json
              FROM ic_html_template
             WHERE html_user_id = :uid
               AND html_template_name = :tname
             LIMIT 1
            """
        ),
        {"uid": user_id, "tname": template_name},
    ).first()

    if not row or row.html_template_json is None:
        return None

    tpl_val = row.html_template_json
    tpl_str = tpl_val if isinstance(tpl_val, str) else json.dumps(
        tpl_val, ensure_ascii=False
    )

    try:
        parsed = json.loads(tpl_str)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed



# FINAL VERSION OF _load_block_template_for_user() IN inbound_settings_postmark.py
def _load_block_template_for_user(
    db: Session,
    user_id: int,
    template_name: Optional[str],
) -> Optional[BlockTemplateModel]:
    """
    Load the block-mapper template for a user, using the *explicitly* selected
    template name.

    We do NOT guess or auto-pick "latest". If no template_name is provided or
    no matching row exists, we return None and the caller decides what to do.
    """
    # If the caller hasn't given us a name, don't try to be clever.
    if not template_name:
        return None

    row = db.execute(
        sqltext(
            """
            SELECT template_json
              FROM ic_pdf_template
             WHERE user_id = :uid
               AND template_name = :tname
             LIMIT 1
            """
        ),
        {"uid": user_id, "tname": template_name},
    ).first()

    if not row or row.template_json is None:
        return None

    tpl_val = row.template_json
    tpl_str = tpl_val if isinstance(tpl_val, str) else json.dumps(
        tpl_val, ensure_ascii=False
    )

    try:
        return _pyd_validate_json(BlockTemplateModel, tpl_str)
    except Exception:
        # Bad JSON in DB – treat as "no usable template"
        return None


def _run_block_template(pdf_bytes: bytes, tpl: BlockTemplateModel) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for f in tpl.fields:
        anchor = getattr(f, "anchor", None)
        trigger_text = (getattr(f, "trigger_text", "") or "").strip()
        direction = (getattr(f, "direction", "") or "").strip().lower()
        if not anchor or not trigger_text or direction not in {"right", "below"}:
            out[f.field_key] = ""
            continue
        try:
            page_num = int(getattr(anchor, "page", None) or getattr(tpl, "page", 1) or 1)
        except Exception:
            page_num = 1
        page_index = max(0, page_num - 1)
        try:
            data = _read_page_blocks_cached(pdf_bytes, page_index)
        except Exception:
            out[f.field_key] = ""
            continue
        blocks = [b for b in data["blocks"] if int(b.get("page", page_index + 1)) == page_num]
        if not blocks:
            out[f.field_key] = ""
            continue
        try:
            anchor_x = float(getattr(anchor, "x", None))
            anchor_y = float(getattr(anchor, "y", None))
        except Exception:
            out[f.field_key] = ""
            continue
        trig_block = _find_best_trigger_block(blocks, trigger_text, (anchor_x, anchor_y))
        if trig_block is None:
            out[f.field_key] = ""
            continue
        try:
            raw_val = _extract_by_trigger_and_direction(
                blocks, float(data["width"]), float(data["height"]),
                trig_block, trigger_text, direction
            )
        except Exception:
            out[f.field_key] = ""
            continue
        try:
            out[f.field_key] = _apply_filter(raw_val, f.filter)
        except Exception:
            out[f.field_key] = (raw_val or "").strip()

    if tpl.customer_map:
        cm = tpl.customer_map
        anchor = getattr(cm, "anchor", None)
        trigger_text = (getattr(cm, "trigger_text", "") or "").strip()
        direction = (getattr(cm, "direction", "") or "").strip().lower()
        if anchor and trigger_text and direction in {"right", "below"}:
            try:
                page_num = int(getattr(anchor, "page", None) or getattr(tpl, "page", 1) or 1)
            except Exception:
                page_num = 1
            page_index = max(0, page_num - 1)
            try:
                data = _read_page_blocks_cached(pdf_bytes, page_index)
                blocks = [b for b in data["blocks"] if int(b.get("page", page_index + 1)) == page_num]
                anchor_x = float(getattr(anchor, "x", None))
                anchor_y = float(getattr(anchor, "y", None))
                trig_block = _find_best_trigger_block(blocks, trigger_text, (anchor_x, anchor_y))
                if trig_block is not None:
                    raw_val = _extract_by_trigger_and_direction(
                        blocks, float(data["width"]), float(data["height"]),
                        trig_block, trigger_text, direction
                    )
                    out["_customer_lookup_value"] = _apply_filter(raw_val, cm.filter)
            except Exception:
                out["_customer_lookup_value"] = ""
    return out

# FINAL VERSION OF _extract_fields_from_queue_row_for_auto()
def _extract_fields_from_queue_row_for_auto(row) -> dict:
    """
    Prefer extracted_text (your parsed mapper output), then fall back to payload_json.
    Supports both top-level keys and {"fields": {...}} wrappers.
    """
    def _parse(obj):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, (bytes, bytearray)):
            try:
                return json.loads(obj.decode("utf-8", errors="replace"))
            except Exception:
                return None
        if isinstance(obj, str):
            try:
                return json.loads(obj)
            except Exception:
                return None
        return None

    # IMPORTANT: prefer extracted_text first
    payload = _parse(getattr(row, "extracted_text", None))
    if payload is None:
        payload = _parse(getattr(row, "payload_json", None)) or {}

    if isinstance(payload, dict) and isinstance(payload.get("fields"), dict):
        return payload["fields"]
    return payload if isinstance(payload, dict) else {}

# --------- NEW: DTOs for toggle ----------
class AutoImportToggleIn(BaseModel):
    enabled: bool

class AutoImportToggleOut(BaseModel):
    enabled: bool

@router.get("/auto-import", response_model=AutoImportToggleOut)
def get_auto_import_toggle(
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    row = db.execute(
        sqltext("SELECT auto_invoice_import FROM account_email_settings WHERE user_id = :uid LIMIT 1"),
        {"uid": user.id},
    ).first()
    val = bool(getattr(row, "auto_invoice_import", 0)) if row else False
    return AutoImportToggleOut(enabled=val)

@router.post("/auto-import", response_model=AutoImportToggleOut)
def set_auto_import_toggle(
    p: AutoImportToggleIn,
    db: Session = Depends(get_db),
    user = Depends(require_user),
):
    db.execute(
        sqltext(
            """
            UPDATE account_email_settings
               SET auto_invoice_import = :on
             WHERE user_id = :uid
            """
        ),
        {"on": 1 if p.enabled else 0, "uid": user.id},
    )
    db.commit()
    return AutoImportToggleOut(enabled=bool(p.enabled))

# --------- NEW: small helpers to validate/insert ----------
def _clean_decimal_str(s: str) -> Decimal:
    raw = (s or "").strip().replace(",", "").replace("£", "")
    if raw == "":
        raise InvalidOperation("empty")
    return Decimal(raw)

def _parse_date_fuzzy(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except Exception:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.replace(".", "/"), fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s.replace("/", "-"))
    except Exception:
        return None

def _parse_json_maybe(s_or_obj: Any) -> Optional[Dict[str, Any]]:
    if s_or_obj is None:
        return None
    if isinstance(s_or_obj, dict):
        return s_or_obj
    if isinstance(s_or_obj, (bytes, bytearray)):
        try:
            return json.loads(s_or_obj.decode("utf-8", errors="replace"))
        except Exception:
            return None
    if isinstance(s_or_obj, str):
        try:
            return json.loads(s_or_obj)
        except Exception:
            return None
    return None

def _auto_promote_if_valid(db: Session, user_id: int, queue_id: int) -> dict:
    """
    Try to convert a single inbound queue row into a real Invoice.
    Returns {"imported": 1} or {"failed": "...reason..."}.
    """
    row = db.execute(
        sqltext(
            """
            SELECT id, payload_json, extracted_text
              FROM inbound_invoice_queue
             WHERE id = :qid AND user_id = :uid
             LIMIT 1
            """
        ),
        {"qid": queue_id, "uid": user_id},
    ).first()
    if not row:
        return {"failed": "not_found"}

    fields = _extract_fields_from_queue_row_for_auto(row)

    inv_no   = (str(fields.get("invoice_number") or "")).strip()
    amt_raw  = fields.get("amount_due")
    issue_s  = (str(fields.get("issue_date") or "")).strip()
    due_s    = (str(fields.get("due_date") or "")).strip()
    cust_val = (str(fields.get("_customer_lookup_value") or "")).strip()
    currency = (str(fields.get("currency") or "GBP") or "GBP").strip()

    if not inv_no:
        return {"failed": "missing_invoice_number"}
    if amt_raw is None:
        return {"failed": "missing_amount_due"}
    if not issue_s:
        return {"failed": "missing_issue_date"}

    cust = (
        db.query(Customer)
          .filter(Customer.user_id == user_id)
          .filter(func.lower(Customer.name) == cust_val.lower())
          .first()
        if cust_val else None
    )
    if not cust:
        return {"failed": "needs_customer"}

    amount = _clean_decimal_str(str(amt_raw))
    issue_dt = _parse_date_fuzzy(issue_s)
    if not issue_dt:
        return {"failed": "bad_issue_date"}

    if due_s:
        due_dt = _parse_date_fuzzy(due_s)
        if not due_dt:
            return {"failed": "bad_due_date"}
    else:
        ttype = cust.terms_type or "net_30"
        tdays = cust.terms_days if (cust.terms_type == "custom") else None
        due_dt = compute_due_date(issue_dt, ttype, tdays)

    # --- NEW: duplicate check (case-insensitive per customer) ---
    exists = (
        db.query(Invoice)
          .filter(Invoice.user_id == user_id)
          .filter(Invoice.customer_id == cust.id)
          .filter(func.lower(Invoice.invoice_number) == inv_no.lower())
          .first()
    )
    if exists:
        return {"failed": "duplicate_invoice_number"}
    # -------------------------------------------------------------

    inv = Invoice(
        user_id=user_id,
        customer_id=cust.id,
        invoice_number=inv_no,
        amount_due=amount,
        currency=currency,
        issue_date=issue_dt,
        due_date=due_dt,
        status="chasing",
        terms_type=cust.terms_type,
        terms_days=cust.terms_days if cust.terms_type == "custom" else None,
    )
    db.add(inv); db.flush()

    db.execute(
        sqltext("DELETE FROM inbound_invoice_queue WHERE id = :id AND user_id = :uid"),
        {"id": queue_id, "uid": user_id},
    )
    return {"imported": 1}


@router.post("/inbound")
async def postmark_inbound(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        data = await request.json()
    except Exception:
        return {"ok": True, "ignored": True, "reason": "bad_json"}

    rcpts = _collect_recipient_addresses(data)
    if not rcpts:
        return {"ok": True, "ignored": True, "reason": "no_recipients"}

    token: Optional[str] = None
    for addr in rcpts:
        token = _extract_token_from_rcpt(addr or "")
        if token:
            break
    if not token:
        return {"ok": True, "queued": False, "reason": "no_token"}

    row = db.execute(
        sqltext(
            """
            SELECT user_id,
                   inbound_active,
                   inbound_reader,
                   inbound_mapping_json,
                   inbound_block_template_name,
                   auto_invoice_import
              FROM account_email_settings
             WHERE inbound_token = :tok
             LIMIT 1
            """
        ),
        {"tok": token},
    ).first()
    if not row:
        return {"ok": True, "queued": False, "reason": "unknown_token"}

    user_id_int = int(row.user_id)

    db.execute(
        sqltext(
            "UPDATE account_email_settings "
            "SET inbound_last_seen_at = UTC_TIMESTAMP() "
            "WHERE inbound_token = :tok"
        ),
        {"tok": token},
    )
    db.commit()

    reader = (row.inbound_reader or "").lower() if row.inbound_reader else ""
    if not bool(row.inbound_active) and reader != "html":
        return {"ok": True, "queued": False, "reason": "intake_disabled"}

    queue_ids: list[int] = []
    reader = (row.inbound_reader or "").lower() if row.inbound_reader else ""

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

    if reader == "html":
        # HTML reader: extract from email body (ignore attachments)
        extracted_text: Optional[str] = None
        html_body = data.get("HtmlBody") or ""
        text_body = data.get("TextBody") or ""

        active_tpl_name = (
            getattr(row, "inbound_block_template_name", None) or ""
        ).strip() or None
        if active_tpl_name:
            tpl = _load_html_template_for_user(db, user_id_int, active_tpl_name)
        else:
            tpl = None

        if tpl:
            if html_body:
                text_for_extract = _html_to_text(html_body)
            else:
                text_for_extract = text_body or ""
            fields_map = _extract_fields_from_html(text_for_extract, html_body, tpl)
            extracted_text = json.dumps(fields_map, ensure_ascii=False)

        ins = db.execute(
            sqltext(
                """
                INSERT INTO inbound_invoice_queue
                    (user_id, source, source_token, original_filename,
                     extracted_text, payload_json, status, error_message)
                VALUES
                    (:uid, 'email', :tok, :fname, :txt, CAST(:payload AS JSON), :st, :err)
                """
            ),
            {
                "uid": user_id_int,
                "tok": token,
                "fname": None,
                "txt": extracted_text,
                "payload": json.dumps(data, ensure_ascii=False),
                "st": "pending",
                "err": None,
            },
        )
        qid = getattr(ins, "lastrowid", None)
        if qid:
            queue_ids.append(int(qid))
        db.commit()
    else:
        # --- NEW: support multiple PDF attachments ---
        attachments = _extract_pdf_attachments(data)

        # If there are PDFs, process each into its own queue row.
        # If there are no PDFs, preserve the previous behaviour (single row with no extracted_text).
        if attachments:
            for pdf_bytes, filename in attachments:
                extracted_text: Optional[str] = None

                if pdf_bytes:
                    tpl_model: Optional[BlockTemplateModel] = None
                    if reader == "pdf":
                        active_tpl_name = (
                            getattr(row, "inbound_block_template_name", None) or ""
                        ).strip() or None
                        if active_tpl_name:
                            tpl_model = _load_block_template_for_user(
                                db, user_id_int, active_tpl_name
                            )

                    if tpl_model is not None:
                        fields_map = _run_block_template(pdf_bytes, tpl_model)
                        extracted_text = json.dumps(fields_map, ensure_ascii=False)
                    else:
                        try:
                            extracted_text = _extract_text(pdf_bytes)
                        except Exception:
                            extracted_text = None

                ins = db.execute(
                    sqltext(
                        """
                        INSERT INTO inbound_invoice_queue
                            (user_id, source, source_token, original_filename,
                             extracted_text, payload_json, status, error_message)
                        VALUES
                            (:uid, 'email', :tok, :fname, :txt, CAST(:payload AS JSON), :st, :err)
                        """
                    ),
                    {
                        "uid": user_id_int,
                        "tok": token,
                        "fname": filename,
                        "txt": extracted_text,
                        "payload": json.dumps(data, ensure_ascii=False),
                        "st": "pending",
                        "err": None,
                    },
                )
                qid = getattr(ins, "lastrowid", None)
                if qid:
                    queue_ids.append(int(qid))

            db.commit()
        else:
            # No PDF attachments – queue a single row.
            ins = db.execute(
                sqltext(
                    """
                    INSERT INTO inbound_invoice_queue
                        (user_id, source, source_token, original_filename,
                         extracted_text, payload_json, status, error_message)
                    VALUES
                        (:uid, 'email', :tok, :fname, :txt, CAST(:payload AS JSON), :st, :err)
                    """
                ),
                {
                    "uid": user_id_int,
                    "tok": token,
                    "fname": None,
                    "txt": None,
                    "payload": json.dumps(data, ensure_ascii=False),
                    "st": "pending",
                    "err": None,
                },
            )
            qid = getattr(ins, "lastrowid", None)
            if qid:
                queue_ids.append(int(qid))
            db.commit()

    # Auto-import if enabled; apply to each queued item.
    if not bool(row.inbound_active):
        return {"ok": True, "queued": True, "preview_only": True, "queued_items": len(queue_ids)}
    if bool(getattr(row, "auto_invoice_import", 0)) and queue_ids:
        imported_count = 0
        first_fail_reason: Optional[str] = None

        for qid in queue_ids:
            try:
                result = _auto_promote_if_valid(db, user_id_int, int(qid))
                if "imported" in result:
                    imported_count += 1
                    db.commit()
                else:
                    reason = str(result.get("failed") or "unknown")
                    if first_fail_reason is None:
                        first_fail_reason = reason
                    db.execute(
                        sqltext(
                            "UPDATE inbound_invoice_queue "
                            "SET error_message = :msg "
                            "WHERE id = :id AND user_id = :uid"
                        ),
                        {"msg": reason[:250], "id": int(qid), "uid": user_id_int},
                    )
                    db.commit()
            except Exception as ex:
                db.rollback()
                reason = f"unexpected:{type(ex).__name__}"
                if first_fail_reason is None:
                    first_fail_reason = reason
                db.execute(
                    sqltext(
                        "UPDATE inbound_invoice_queue "
                        "SET error_message = :msg "
                        "WHERE id = :id AND user_id = :uid"
                    ),
                    {"msg": reason[:250], "id": int(qid), "uid": user_id_int},
                )
                db.commit()

        if imported_count and not first_fail_reason:
            # All attachments imported cleanly.
            return {
                "ok": True,
                "queued": False,
                "auto_imported": imported_count,
            }
        else:
            # Some or all attachments failed auto-import; they remain queued.
            return {
                "ok": True,
                "queued": True,
                "auto_import_failed": first_fail_reason or "unknown",
            }

    # No auto-import: everything is just queued for manual review.
    return {
        "ok": True,
        "queued": True,
        "user_id": user_id_int,
        "reader": row.inbound_reader or None,
        "queued_items": len(queue_ids),
    }
