from fastapi import FastAPI, HTTPException

from app.db import create_db_and_tables
from app.routes import cleanup, config_validation, openstack, provider, scenarios
from app.routes.topology import router as topology_router
from app.schemas import DeploymentPlan, TopologyInput
from app.topology_compiler import compile_topology


app = FastAPI(title="CloudNet Testbed")
app.include_router(openstack.router)
app.include_router(provider.router)
app.include_router(topology_router)
app.include_router(scenarios.router)
app.include_router(config_validation.router)
app.include_router(cleanup.router)


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()
    try:
        from app.services.local_state_store import load_local_state_on_startup

        load_local_state_on_startup()
    except Exception:
        pass


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/compile", response_model=DeploymentPlan)
def compile_endpoint(topology: TopologyInput) -> dict:
    try:
        topology_data = topology.model_dump(by_alias=True)
        return compile_topology(topology_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
