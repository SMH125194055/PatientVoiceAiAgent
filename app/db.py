"""Database engine and session factory.

Why NullPool: on Vercel each request may run in a fresh serverless instance, so
an app-side connection pool would hold idle connections that never get reused.
Neon's `-pooler` endpoint (PgBouncer) does the pooling for us instead.
SQLite (used only by the test suite) gets a StaticPool so the in-memory
database is shared across threads.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(
            url, connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
