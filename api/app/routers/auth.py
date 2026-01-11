# FINAL VERSION OF api/app/routers/auth.py
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ..database import get_db
from ..models import User, VerificationToken
from ..shared import templates
from ..security import verify_password, hash_password
from ..initial_user_setup import run_initial_user_setup

import os 

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "ic_session"

OWNER_EMAIL = os.getenv("IC_OWNER_EMAIL", "admin@remindandpay.com").strip().lower()

def set_session(resp: RedirectResponse, user_id: int, remember: bool):
    max_age = 60 * 60 * 24 * 14 if remember else None
    resp.set_cookie(
        COOKIE_NAME,
        str(user_id),
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=False,
    )

def clear_session(resp: RedirectResponse):
    resp.delete_cookie(COOKIE_NAME)

def get_uid_from_cookie(request: Request) -> Optional[int]:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        return int(raw)
    except:
        return None

# --- Login ---

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard"):
    return templates.TemplateResponse(
        "auth/auth_login.html",
        {"request": request, "next": next, "error": None},
    )

@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    remember: Optional[str] = Form(None),
    next: str = Form("/dashboard"),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()

    user = (
        db.query(User)
          .filter(User.email == email, User.is_active == True)  # noqa: E712
          .first()
    )

    if not user or not verify_password(password, user.password_hash or ""):
        return templates.TemplateResponse(
            "auth/auth_login.html",
            {"request": request, "next": next, "error": "Invalid email or password"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    resp = RedirectResponse(url=next or "/dashboard", status_code=status.HTTP_302_FOUND)
    set_session(resp, user.id, remember is not None)
    return resp

@router.post("/logout")
def logout(next: str = "/auth/login"):
    resp = RedirectResponse(url=next, status_code=status.HTTP_302_FOUND)
    clear_session(resp)
    return resp

# Dependency for protected routes
def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = get_uid_from_cookie(request)
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/auth/login?next={request.url.path}"},
        )

    user = db.get(User, uid)
    if not user or not getattr(user, "is_active", False):
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/auth/login"},
        )
    return user

# --- Registration & email verification ---

@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, next: str = "/dashboard"):
    return templates.TemplateResponse(
        "auth/auth_register.html",
        {"request": request, "next": next, "error": None},
    )

@router.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    next: str = Form("/dashboard"),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()
    if not email or not password:
        return templates.TemplateResponse(
            "auth/auth_register.html",
            {"request": request, "next": next, "error": "Email and password are required."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if password != confirm_password:
        return templates.TemplateResponse(
            "auth/auth_register.html",
            {"request": request, "next": next, "error": "Passwords do not match."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # create user inactive until verified
    try:
        user = User(email=email, password_hash=hash_password(password), is_active=False)
        db.add(user)
        db.flush()  # get user.id
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(
            "auth/auth_register.html",
            {"request": request, "next": next, "error": "That email is already registered. Try logging in."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # create token
    token = VerificationToken.new(user_id=user.id, hours=24)
    db.add(token)
    db.commit()

    # testing shortcut: show the link
    verify_url = f"/auth/verify?token={token.token}&next={next}"
    return templates.TemplateResponse(
        "auth/auth_verify_sent.html",
        {"request": request, "next": next, "email": email, "verify_url": verify_url},
    )

@router.get("/verify", response_class=HTMLResponse)
def verify_email(request: Request, token: str, next: str = "/dashboard", db: Session = Depends(get_db)):
    t = db.query(VerificationToken).filter(VerificationToken.token == token).first()
    if not t or t.purpose != "verify_email":
        return templates.TemplateResponse(
            "auth/auth_verify_done.html",
            {"request": request, "ok": False, "message": "Invalid or unknown token.", "next": next},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.utcnow()
    if t.used_at is not None:
        return templates.TemplateResponse(
            "auth/auth_verify_done.html",
            {"request": request, "ok": True, "message": "Email already verified. You can log in now.", "next": next},
        )

    if now > t.expires_at:
        return templates.TemplateResponse(
            "auth/auth_verify_done.html",
            {"request": request, "ok": False, "message": "This verification link has expired. Please register again.", "next": "/auth/register"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = db.get(User, t.user_id)
    if not user:
        return templates.TemplateResponse(
            "auth/auth_verify_done.html",
            {"request": request, "ok": False, "message": "User not found.", "next": "/auth/register"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.is_active = True
    t.used_at = now
    db.add_all([user, t])
    db.commit()

    run_initial_user_setup(db, user.id, seed_templates=True)

    return templates.TemplateResponse(
        "auth/auth_verify_done.html",
        {"request": request, "ok": True, "message": "Your email has been verified. You can log in now.", "next": "/auth/login"},
    )


# --- Owner-only dependency ---

def require_owner(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Owner guard for admin screens.
    Only allows the configured owner account (IC_OWNER_EMAIL env var).
    """
    user = require_user(request, db)
    if (user.email or "").strip().lower() != OWNER_EMAIL:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    return user

