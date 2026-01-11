# app/database.py   
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from pathlib import Path

env_path = Path(__file__).resolve().parents[2] / "config" / ".env"
if env_path.exists():
    load_dotenv(env_path.as_posix(), override=True, encoding="utf-8-sig")

DB_URL = os.getenv("DB_URL")
if not DB_URL:
    raise RuntimeError("DB_URL not set in config/.env")

engine = create_engine(DB_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
