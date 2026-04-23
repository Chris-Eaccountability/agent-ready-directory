"""
conftest.py — Shared pytest fixtures.

Each test gets a fresh in-memory SQLite database so tests are fully isolated.
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient

from app.db import init_db, set_connection
from app.seed import run_seed


@pytest.fixture()
def db_conn():
    """Fresh in-memory SQLite connection for each test."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    set_connection(conn)
    yield conn
    conn.close()


@pytest.fixture()
def seeded_db(db_conn):
    """In-memory DB with seed data already inserted (no verifier run)."""
    run_seed(db_conn)
    yield db_conn


@pytest.fixture()
def client(seeded_db):
    """TestClient backed by a seeded in-memory database."""
    from app.server import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def empty_client(db_conn):
    """TestClient backed by an empty (no seed data) in-memory database."""
    from app.server import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
