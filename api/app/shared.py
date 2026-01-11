# api/app/shared.py
from pathlib import Path

# FastAPI / Starlette bits you commonly use
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from fastapi.templating import Jinja2Templates

# Pydantic
from pydantic import BaseModel, Field, EmailStr

# SQLAlchemy session type
from sqlalchemy.orm import Session

# Where templates live
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "web" / "templates"

# Single Jinja2Templates instance shared across routers
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Re-export for convenience
__all__ = [
    "APIRouter",
    "Depends",
    "HTTPException",
    "Query",
    "BaseModel",
    "Field",
    "EmailStr",
    "Session",
    "templates",
]
