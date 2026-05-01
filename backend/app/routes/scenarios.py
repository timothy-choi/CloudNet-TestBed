import json
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session

from app.db import get_session
from app.schemas import TopologyInput
from app.services.scenario_service import ScenarioError, ScenarioRunner


class ScenarioMeta(BaseModel):
    name: str


class ScenarioRunBody(BaseModel):
    scenario: ScenarioMeta
    topology: TopologyInput
    steps: list[Any] = Field(default_factory=list)


router = APIRouter(prefix="/scenarios", tags=["scenarios"])

_YAML_MEDIA_TYPES = frozenset(
    {
        "application/x-yaml",
        "application/yaml",
        "text/yaml",
    }
)


@router.post("/run")
async def run_scenario_endpoint(
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Run a scenario from JSON or YAML (`Content-Type: application/x-yaml`)."""
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty body")
    ct = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    text = raw.decode()
    try:
        if ct in _YAML_MEDIA_TYPES:
            payload = yaml.safe_load(text)
        else:
            payload = json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be a mapping")
    try:
        body = ScenarioRunBody.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    try:
        return ScenarioRunner(session).run(
            scenario_name=body.scenario.name,
            topology_input=body.topology,
            raw_steps=body.steps,
        )
    except ScenarioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
