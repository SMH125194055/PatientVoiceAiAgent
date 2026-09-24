"""Dashboard page, recent calls feed and health check."""
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import service
from app.config import settings
from app.db import get_db
from app.errors import envelope
from app.schemas import CallLogOut

router = APIRouter()

_DASHBOARD = (Path(__file__).resolve().parent.parent / "static" / "dashboard.html").read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse(_DASHBOARD.replace("{{INTAKE_PHONE}}", settings.intake_phone_number))


@router.get("/calls", tags=["calls"])
def recent_calls(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    calls = service.list_calls(db, limit=limit)
    return envelope([CallLogOut.model_validate(c).model_dump(mode="json") for c in calls])


@router.get("/health", tags=["health"])
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return envelope({"status": "ok", "database": "ok"})
