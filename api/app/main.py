from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from .shared import templates

from .routers.customers import router as customers_router
from .routers.invoices import router as invoices_router
from .routers.debug_list import router as debug_router
from .routers.dashboard import router as dashboard_router
from .routers.payments import router as payments_router
from .routers.statements import router as statements_router
from .routers.settings import router as settings_router
from .routers.email_settings import router as email_settings_router
from .routers import auth as auth_router
from .routers.auth import require_user
from .routers.statement_reminders import router as statement_reminders_router
from .routers.chasing_messages import router as chasing_messages_router
from .routers.postmark_webhooks import router as postmark_webhooks_router
from .routers.outbox import router as outbox_router
from .routers.statement_globals import router as statement_globals_router
from .routers import reminder_templates
from .routers import chasing_plans
from .routers import chasing_reminders 
from .routers.email_domains import router as email_domains_router
from .routers.postmark_servers import router as postmark_servers_router
from .routers.inbound_settings_app import router as inbound_settings_app_router
from .routers.inbound_settings_postmark import router as inbound_settings_postmark_router
from .routers.inbound_pdf import router as inbound_pdf_router
from .routers.inbound_pdf_templates import router as inbound_pdf_templates_router
from .routers.extractor_line_regions import router as extractor_line_regions_router
from .routers.inbound_pdf_blocks import router as inbound_pdf_blocks_router
from .routers.inbound_html_templates import router as inbound_html_templates_router
from .routers.inbound_invoice_queue import router as inbound_invoice_queue_router
from .routers.admin_app import router as admin_router
from .routers.sms_settings import router as sms_settings_router
from .routers.sms_pricing import router as sms_pricing_router
from .routers.sms_webhooks import router as sms_webhooks_router

from .models import Base, Customer
from .database import engine, get_db

app = FastAPI(title="Remind & Pay minimal API") 

# --- Include API routers (unchanged) ---
app.include_router(settings_router)
app.include_router(customers_router)
app.include_router(invoices_router)
app.include_router(debug_router)
app.include_router(dashboard_router)
app.include_router(payments_router)
app.include_router(statements_router)
app.include_router(email_settings_router)
app.include_router(statement_reminders_router)
app.include_router(chasing_messages_router)
app.include_router(postmark_webhooks_router)
app.include_router(outbox_router)
app.include_router(statement_globals_router)
app.include_router(reminder_templates.router)
app.include_router(chasing_plans.router)
app.include_router(chasing_reminders.router)
app.include_router(email_domains_router)   
app.include_router(postmark_servers_router)
app.include_router(inbound_settings_app_router)         
app.include_router(inbound_settings_postmark_router)   
app.include_router(inbound_pdf_router)
app.include_router(inbound_pdf_templates_router)  
app.include_router(extractor_line_regions_router)
app.include_router(inbound_pdf_blocks_router)
app.include_router(inbound_html_templates_router)
app.include_router(inbound_invoice_queue_router)
app.include_router(admin_router)
app.include_router(sms_settings_router)
app.include_router(sms_pricing_router)
app.include_router(sms_webhooks_router)

# /auth endpoints (login/logout)
app.include_router(auth_router.router)

# --- Paths for web assets ---
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "web" / "static"

# --- Static + templates ---
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ------------------------------
#        PAGE ROUTES
# ------------------------------

@app.on_event("startup")
def ensure_tables():
    Base.metadata.create_all(bind=engine)

# Public, minimal �please log in� page (no navbar)
# NOTE: route path must be a URL path, not a template name.
@app.get("/auth/required", include_in_schema=False)
def auth_required_page(request: Request, next: str = "/dashboard"):
    # you moved templates under templates/auth/
    return templates.TemplateResponse(
        "auth/auth_required.html",
        {"request": request, "next": next},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )

# Global handler: convert unauthenticated errors into our minimal page
@app.exception_handler(HTTPException)
async def friendly_auth_handler(request: Request, exc: HTTPException):
    loc = (exc.headers or {}).get("Location") if hasattr(exc, "headers") else None
    unauth = exc.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
    redirect_to_login = exc.status_code == status.HTTP_307_TEMPORARY_REDIRECT and (loc or "").startswith("/auth/login")

    # For API paths, return JSON 401 so clients don�t get HTML
    if request.url.path.startswith("/api/") and (unauth or redirect_to_login):
        return JSONResponse({"detail": "Not authenticated"}, status_code=status.HTTP_401_UNAUTHORIZED)

    # For page paths, show the friendly auth page (using new template path)
    if unauth or redirect_to_login:
        return templates.TemplateResponse(
            "auth/auth_required.html",
            {"request": request, "next": request.url.path},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Everything else: default JSON
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

# Protected pages
@app.get("/")
@app.get("/dashboard")
def dashboard(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "active": "dashboard"})

@app.get("/invoices")
def invoices_page(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse("invoices.html", {"request": request, "active": "invoices"})

@app.get("/customers")
def customers_page(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse("customers.html", {"request": request, "active": "customers"})

@app.get("/schedule")
def schedule_page(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse("schedule.html", {"request": request, "active": "schedule"})

@app.get("/message_templates")
def message_templates_page(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse("message_templates.html", {"request": request, "active": "message_templates"})

@app.get("/settings")
def settings_page(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse("settings.html", {"request": request, "active": ""})

@app.get("/support")
def support_page(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse("settings.html", {"request": request, "active": "", "title": "Support � Invoice Chaser"})

# Customer pages
@app.get("/customers/{customer_id}")
def customer_dashboard_page(
    request: Request,
    customer_id: int,
    db = Depends(get_db),
    user = Depends(require_user),
):
    cust = db.query(Customer).filter(Customer.id == customer_id).first()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    return templates.TemplateResponse("customer_dashboard.html", {"request": request, "active": "dashboard", "customer": cust})

@app.get("/customers/{customer_id}/statement")
def customer_statement_page(
    request: Request,
    customer_id: int,
    db = Depends(get_db),
    user = Depends(require_user),
):
    cust = db.query(Customer).filter(Customer.id == customer_id).first()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    return templates.TemplateResponse("customer_statement.html", {"request": request, "active": "customers", "customer": cust})

# FINAL VERSION OF invoice-import settings page route in main.py

@app.get("/settings/invoice-import")
def settings_invoice_import_page(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse(
        "settings_invoice_import.html",
        {
            "request": request,
            "active": ""
        },
    )


@app.get("/settings/html-import")
def settings_html_import_page(request: Request, user = Depends(require_user)):
    return templates.TemplateResponse(
        "settings_html_import.html",
        {
            "request": request,
            "active": ""
        },
    )
