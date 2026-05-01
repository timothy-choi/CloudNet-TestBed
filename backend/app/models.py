from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column
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
    firewall_rules: list["FirewallRule"] = Relationship(
        back_populates="topology",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    resources: list["DeploymentResource"] = Relationship(
        back_populates="topology",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    connectivity_tests: list["ConnectivityTest"] = Relationship(
        back_populates="topology",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    failure_events: list["FailureEvent"] = Relationship(
        back_populates="topology",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    events: list["Event"] = Relationship(
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


class FirewallRule(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.id")
    name: str
    protocol: str
    port: int | None = None
    from_node: str
    to_node: str

    topology: Topology | None = Relationship(back_populates="firewall_rules")


class DeploymentResource(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.id")
    resource_type: str
    resource_name: str
    openstack_id: str
    created_at: datetime = Field(default_factory=utc_now)

    topology: Topology | None = Relationship(back_populates="resources")


class ConnectivityTest(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.id")
    source_node: str
    target_node: str
    test_type: str = "ping"
    status: str = "PENDING"
    output: str
    created_at: datetime = Field(default_factory=utc_now)

    topology: Topology | None = Relationship(back_populates="connectivity_tests")


class FailureEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.id")
    target_type: str
    target_name: str
    action: str
    status: str
    output: str
    created_at: datetime = Field(default_factory=utc_now)

    topology: Topology | None = Relationship(back_populates="failure_events")


class Event(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    topology_id: int = Field(foreign_key="topology.id")
    timestamp: datetime = Field(default_factory=utc_now)
    type: str
    status: str
    message: str
    event_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON),
    )

    topology: Topology | None = Relationship(back_populates="events")
