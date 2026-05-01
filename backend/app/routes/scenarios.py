from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import get_session
from app.schemas import TopologyInput
from app.services.scenario_service import ScenarioError, run_scenario


class ScenarioMeta(BaseModel):
    name: str


class ScenarioRunBody(BaseModel):
    scenario: ScenarioMeta
    topology: TopologyInput
    steps: list[Any] = Field(default_factory=list)


router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.post("/run")
def run_scenario_endpoint(
    body: ScenarioRunBody,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        return run_scenario(
            session,
            scenario_name=body.scenario.name,
            topology_input=body.topology,
            raw_steps=body.steps,
        )
    except ScenarioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
