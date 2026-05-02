from typing import Any

from sqlmodel import Session, select

from app.models import Event


def emit_event(
    session: Session,
    topology_id: int,
    event_type: str,
    status: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> Event:
    from app.services.trace_context import current_trace_metadata

    merged: dict[str, Any] = dict(metadata or {})
    for k, v in current_trace_metadata().items():
        merged.setdefault(k, v)
    merged.setdefault("topology_id", topology_id)

    event = Event(
        topology_id=topology_id,
        type=event_type,
        status=status,
        message=message,
        event_metadata=merged,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def list_events(
    session: Session,
    topology_id: int,
    limit: int | None = None,
    reverse: bool = False,
) -> list[Event]:
    order_column = Event.timestamp.desc() if reverse else Event.timestamp
    statement = (
        select(Event)
        .where(Event.topology_id == topology_id)
        .order_by(order_column, Event.id.desc() if reverse else Event.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.exec(statement).all())


def serialize_event(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "topology_id": event.topology_id,
        "timestamp": event.timestamp,
        "type": event.type,
        "status": event.status,
        "message": event.message,
        "metadata": event.event_metadata,
    }
