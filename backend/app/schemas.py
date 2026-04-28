from pydantic import BaseModel, Field


class TopologyNode(BaseModel):
    name: str
    type: str


class TopologyLink(BaseModel):
    from_node: str = Field(alias="from")
    to: str
    subnet: str


class TopologyInput(BaseModel):
    name: str
    nodes: list[TopologyNode]
    links: list[TopologyLink] = []


class DeploymentNetwork(BaseModel):
    name: str
    subnet: str
    attached_nodes: list[str]


class DeploymentServer(BaseModel):
    name: str
    type: str


class DeploymentPlan(BaseModel):
    topology_name: str
    networks: list[DeploymentNetwork]
    servers: list[DeploymentServer]
