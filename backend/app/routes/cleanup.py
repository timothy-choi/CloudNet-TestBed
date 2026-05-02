"""Cleanup janitor API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import get_session
from app.services.cleanup_janitor import run_cleanup_janitor

router = APIRouter(prefix="/cleanup", tags=["cleanup"])


@router.post("/janitor")
def run_janitor_endpoint(session: Session = Depends(get_session)) -> dict:
    """Best-effort teardown for orphaned provider resources (see ``state.json``)."""
    return run_cleanup_janitor(session)
