from collections.abc import Generator

import app.models  # noqa: F401 — register SQLModel tables
from sqlmodel import Session, SQLModel, create_engine


DATABASE_URL = "sqlite:///./cloudnet.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def SessionLocal() -> Session:
    return Session(engine)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
