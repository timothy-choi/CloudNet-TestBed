from fastapi import FastAPI, HTTPException

from app.schemas import DeploymentPlan, TopologyInput
from app.topology_compiler import compile_topology


app = FastAPI(title="CloudNet Testbed")


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
