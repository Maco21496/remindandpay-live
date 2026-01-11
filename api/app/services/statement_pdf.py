# app/services/statement_pdf.py
from typing import Optional
from pathlib import Path
import os
from base64 import b64encode
import logging

from sqlalchemy.orm import Session
import requests

from ..models import AppSettings, Customer
from ..shared import templates
from .statements_logic import compute_statement_summary

log = logging.getLogger("statement_pdf")


def render_statement_pdf_html(
    db: Session,
    user_id: int,
    customer_id: int,
    date_to: Optional[str] = None,
    include_after_payments: bool = False,
) -> Optional[str]:
    """
    Build a minimal, self-contained HTML for the customer's statement suitable for PDF rendering.
    Returns the HTML string or None if data can't be prepared.
    """
    try:
        # Branding
        org = db.query(AppSettings).filter(AppSettings.user_id == user_id).first()
        org_addr = getattr(org, "org_address", None) or ""
        org_logo = getattr(org, "org_logo_url", None) or None
        org_logo_data_uri: Optional[str] = None
        org_logo_file_url: Optional[str] = None

        # Try to embed logo as data URI (local static or HTTP)
        try:
            if isinstance(org_logo, str) and org_logo:
                project_root = Path(__file__).resolve().parents[2]
                raw: bytes | None = None
                mime = "image/png"

                if org_logo.startswith("/static/"):
                    logo_fs = project_root / org_logo.lstrip("/")
                    if logo_fs.is_file():
                        raw = logo_fs.read_bytes()
                        ext = (logo_fs.suffix or ".png").lower().lstrip(".")
                        mime = {
                            "jpg": "image/jpeg", "jpeg": "image/jpeg",
                            "png": "image/png",  "gif": "image/gif",
                            "svg": "image/svg+xml",
                        }.get(ext, "image/png")
                        try:
                            org_logo_file_url = "file:///" + str(logo_fs.resolve()).replace('\\','/')
                        except Exception:
                            pass
                elif org_logo.lower().startswith(("http://", "https://")):
                    try:
                        r = requests.get(org_logo, timeout=5)
                        if r.ok:
                            raw = r.content
                            # crude mime guess from URL
                            for ext, mm in {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","gif":"image/gif","svg":"image/svg+xml"}.items():
                                if org_logo.lower().endswith(ext):
                                    mime = mm; break
                    except Exception:
                        pass
                elif "static/" in org_logo:
                    # handle cases like 'static/uploads/logo/x.png'
                    logo_fs = project_root / org_logo
                    if logo_fs.is_file():
                        raw = logo_fs.read_bytes()
                        ext = (logo_fs.suffix or ".png").lower().lstrip(".")
                        mime = {
                            "jpg": "image/jpeg", "jpeg": "image/jpeg",
                            "png": "image/png",  "gif": "image/gif",
                            "svg": "image/svg+xml",
                        }.get(ext, "image/png")
                        try:
                            org_logo_file_url = "file:///" + str(logo_fs.resolve()).replace('\\','/')
                        except Exception:
                            pass

                if raw:
                    org_logo_data_uri = f"data:{mime};base64,{b64encode(raw).decode('ascii')}"
        except Exception:
            pass

        cust = (
            db.query(Customer)
              .filter(Customer.id == customer_id, Customer.user_id == user_id)
              .first()
        )
        if not cust:
            return None

        summary = compute_statement_summary(
            db=db,
            user_id=user_id,
            customer_id=customer_id,
            date_to=date_to,
            include_after_payments=include_after_payments,
        )

        tpl = templates.env.get_template("pdf/statement_pdf.html")
        html = tpl.render(
            org_address=org_addr,
            org_logo_url=org_logo,
            org_logo_data_uri=org_logo_data_uri,
            org_logo_file_url=org_logo_file_url,
            customer=cust,
            summary=summary,
        )
        return html
    except Exception as e:
        log.warning("Failed to render statement HTML: %s", e)
        return None


def render_pdf_from_html(html: str) -> Optional[bytes]:
    """
    HTML -> PDF using wkhtmltopdf (via pdfkit). Returns PDF bytes or None on failure.
    Configure binary via env WKHTMLTOPDF_PATH or ensure it's on PATH.
    """
    try:
        import pdfkit
        exe = os.getenv("WKHTMLTOPDF_PATH")
        cfg = pdfkit.configuration(wkhtmltopdf=exe) if exe else None
        options = {"quiet": "", "enable-local-file-access": ""}
        pdf_bytes = pdfkit.from_string(html, False, configuration=cfg, options=options)
        if isinstance(pdf_bytes, (bytes, bytearray)) and pdf_bytes:
            return bytes(pdf_bytes)
    except Exception as e:
        log.warning("wkhtmltopdf PDF render failed: %s", e)
    return None
