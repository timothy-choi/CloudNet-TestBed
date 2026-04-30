from datetime import datetime, timezone

from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Topology(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    status: str = "CREATED"
    created_at: datetime = Field(default_factory=utc_now)

    nodes: list["Node"] = Relationship(
        back_populates="topology",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    links: list["Link"] = Relationship(
        back_populates="topology",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    resources: list["DeploymentResource"] = Relationship(
        back_populates="topology",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Node(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.id")
    name: str
    type: str

    topology: Topology | None = Relationship(back_populates="nodes")


class Link(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.id")
    from_node: str
    to_node: str
    subnet: str

    topology: Topology | None = Relationship(back_populates="links")


class DeploymentResource(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.id")
    resource_type: str
    resource_name: str
    openstack_id: str
    created_at: datetime = Field(default_factory=utc_now)

    topology: Topology | None = Relationship(back_populates="resources")
