"""Test setup: an in-memory SQLite database, recreated for every test, so the
suite never touches the real Neon database and needs no network."""
import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["VAPI_WEBHOOK_SECRET"] = "test-secret"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

VALID_PATIENT = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "03/15/1985",
    "sex": "female",
    "phone_number": "(512) 555-0142",
    "address_line_1": "742 Evergreen Terrace",
    "city": "Austin",
    "state": "Texas",
    "zip_code": "78701",
}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def patient(client):
    return client.post("/patients", json=VALID_PATIENT).json()["data"]


def vapi_tool_call(name: str, args: dict, caller: str = "+15125550142", call_id: str = "call-1") -> dict:
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id, "customer": {"number": caller}},
            "toolCallList": [{"id": "tc-1", "type": "function", "function": {"name": name, "arguments": args}}],
        }
    }
