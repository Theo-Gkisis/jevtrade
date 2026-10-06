"""Database connection (Supabase Postgres via the session pooler). The URL comes from DATABASE_URL in .env."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text


def database_url() -> str:
    """DATABASE_URL from the environment, forced to use the psycopg (v3) driver."""
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL missing (check .env)")
    # Supabase gives postgresql://...; SQLAlchemy needs to be told which driver to use
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def create_db_engine() -> Engine:
    # pool_pre_ping: test a pooled connection before using it, in case the server closed it
    return create_engine(database_url(), pool_pre_ping=True)


if __name__ == "__main__":
    engine = create_db_engine()
    with engine.connect() as conn:
        version = conn.execute(text("select version()")).scalar_one()
        now = conn.execute(text("select now()")).scalar_one()
    print(f"Connected to {engine.url.host}")
    print(f"Server time: {now}")
    print(f"Version:     {version[:60]}...")
