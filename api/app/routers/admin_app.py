# FINAL VERSION OF api/app/routers/admin_app.py
from typing import List

from fastapi import APIRouter, Depends, Request, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..shared import templates
from .auth import require_owner

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_class=HTMLResponse)
def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    """
    Owner-only management screen for all users.
    Shows basic info and allows pausing / unpausing / deactivating accounts.
    """
    users: List[User] = (
        db.query(User)
          .order_by(User.created_at.desc())
          .all()
    )

    return templates.TemplateResponse(
        "admin_users.html",  # CHANGED: matches templates/admin_users.html
        {
            "request": request,
            "users": users,
            "owner_email": (owner.email or "").strip().lower(),
        },
    )


@router.post("/users/{user_id}/pause")
def admin_pause_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    """
    Soft-pause a user: sets is_active = 0.
    User will not be able to log in.
    """
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Never allow pausing the owner account
    if (target.email or "").strip().lower() == "admin@remindandpay.com":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot modify owner account")

    target.is_active = False
    db.add(target)
    db.commit()

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{user_id}/unpause")
def admin_unpause_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    """
    Unpause a user: sets is_active = 1.
    """
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target.is_active = True
    db.add(target)
    db.commit()

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/{user_id}/deactivate")
def admin_deactivate_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
):
    """
    "Delete" action implemented as a soft deactivation using is_active = 0.
    This avoids problems with foreign-key references in other tables.
    """
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Never allow deleting the owner account
    if (target.email or "").strip().lower() == "admin@remindandpay.com":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot modify owner account")

    target.is_active = False
    db.add(target)
    db.commit()

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)
