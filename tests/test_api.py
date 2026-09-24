import json
from datetime import date, timedelta

from tests.conftest import VALID_PATIENT, vapi_tool_call

SECRET = {"x-vapi-secret": "test-secret"}


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #
def test_create_patient_normalizes_input(client):
    res = client.post("/patients", json=VALID_PATIENT)
    assert res.status_code == 201
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    assert data["patient_id"]
    assert data["phone_number"] == "5125550142"
    assert data["state"] == "TX"
    assert data["sex"] == "Female"
    assert data["date_of_birth"] == "03/15/1985"
    assert data["preferred_language"] == "English"


def test_future_date_of_birth_is_rejected(client):
    future = (date.today() + timedelta(days=30)).strftime("%m/%d/%Y")
    res = client.post("/patients", json={**VALID_PATIENT, "date_of_birth": future})
    assert res.status_code == 422
    fields = {d["field"]: d["message"] for d in res.json()["error"]["details"]}
    assert fields["date_of_birth"] == "cannot be in the future"


def test_short_phone_number_is_rejected(client):
    res = client.post("/patients", json={**VALID_PATIENT, "phone_number": "555"})
    assert res.status_code == 422
    assert res.json()["error"]["details"][0]["field"] == "phone_number"


def test_invalid_state_zip_and_missing_field(client):
    bad = {**VALID_PATIENT, "state": "ZZ", "zip_code": "1234"}
    del bad["city"]
    res = client.post("/patients", json=bad)
    assert res.status_code == 422
    fields = {d["field"] for d in res.json()["error"]["details"]}
    assert {"state", "zip_code", "city"} <= fields


def test_unknown_field_is_rejected(client):
    res = client.post("/patients", json={**VALID_PATIENT, "patient_id": "hack"})
    assert res.status_code == 422


def test_malformed_json_returns_400(client):
    res = client.post("/patients", content="{not json", headers={"content-type": "application/json"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_json"


def test_get_patient_and_not_found(client, patient):
    assert client.get(f"/patients/{patient['patient_id']}").status_code == 200
    assert client.get("/patients/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/patients/not-a-uuid").status_code == 400


def test_list_filters(client, patient):
    client.post("/patients", json={**VALID_PATIENT, "last_name": "Smith", "phone_number": "2125550100"})
    assert len(client.get("/patients").json()["data"]) == 2
    assert len(client.get("/patients?last_name=doe").json()["data"]) == 1
    assert len(client.get("/patients?phone_number=512-555-0142").json()["data"]) == 1
    assert len(client.get("/patients?date_of_birth=1985-03-15").json()["data"]) == 2
    assert client.get("/patients?phone_number=123").status_code == 422


def test_partial_update(client, patient):
    pid = patient["patient_id"]
    res = client.put(f"/patients/{pid}", json={"last_name": "Davis", "email": "Jane@Example.com"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["last_name"] == "Davis"
    assert data["email"] == "jane@example.com"
    assert data["first_name"] == "Jane"  # untouched


def test_update_cannot_clear_required_field(client, patient):
    res = client.put(f"/patients/{patient['patient_id']}", json={"first_name": None})
    assert res.status_code == 422


def test_empty_update_returns_400(client, patient):
    assert client.put(f"/patients/{patient['patient_id']}", json={}).status_code == 400


def test_soft_delete(client, patient):
    pid = patient["patient_id"]
    res = client.delete(f"/patients/{pid}")
    assert res.status_code == 200
    assert res.json()["data"]["deleted_at"]
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["data"] == []
    assert client.delete(f"/patients/{pid}").status_code == 404


def test_health_and_dashboard(client):
    assert client.get("/health").json()["data"]["status"] == "ok"
    assert "Patient registry" in client.get("/").text


# --------------------------------------------------------------------------- #
# Vapi webhook + voice tools
# --------------------------------------------------------------------------- #
def _tool_result(res) -> dict:
    return json.loads(res.json()["results"][0]["result"])


def test_webhook_requires_secret(client):
    res = client.post("/vapi/webhook", json=vapi_tool_call("lookup_patient", {}))
    assert res.status_code == 401


def test_lookup_uses_caller_id(client, patient):
    res = client.post("/vapi/webhook", json=vapi_tool_call("lookup_patient", {}), headers=SECRET)
    result = _tool_result(res)
    assert result["status"] == "found"
    assert result["matches"][0]["first_name"] == "Jane"


def test_lookup_not_found(client):
    res = client.post("/vapi/webhook", json=vapi_tool_call("lookup_patient", {"phone_number": "2125550100"}), headers=SECRET)
    assert _tool_result(res)["status"] == "not_found"


def test_register_via_tool_and_validation_feedback(client):
    bad = {**VALID_PATIENT, "date_of_birth": "13/45/1990"}
    result = _tool_result(client.post("/vapi/webhook", json=vapi_tool_call("register_patient", bad), headers=SECRET))
    assert result["status"] == "validation_error"
    assert result["errors"][0]["field"] == "date_of_birth"

    # Vapi sometimes sends arguments as a JSON string
    res = client.post("/vapi/webhook", json=vapi_tool_call("register_patient", json.dumps(VALID_PATIENT)), headers=SECRET)
    result = _tool_result(res)
    assert result["status"] == "saved"
    assert client.get(f"/patients/{result['patient_id']}").status_code == 200


def test_update_via_tool(client, patient):
    args = {"patient_id": patient["patient_id"], "last_name": "Davis"}
    result = _tool_result(client.post("/vapi/webhook", json=vapi_tool_call("update_patient", args), headers=SECRET))
    assert result["status"] == "updated"
    assert client.get(f"/patients/{patient['patient_id']}").json()["data"]["last_name"] == "Davis"


def test_book_appointment_flow(client, patient):
    slots = _tool_result(client.post("/vapi/webhook", json=vapi_tool_call("get_available_slots", {}), headers=SECRET))
    assert slots["status"] == "ok"
    slot_id = slots["slots"][0]["slot_id"]
    args = {"patient_id": patient["patient_id"], "slot_id": slot_id, "reason": "New patient visit"}
    booked = _tool_result(client.post("/vapi/webhook", json=vapi_tool_call("book_appointment", args), headers=SECRET))
    assert booked["status"] == "booked"
    again = _tool_result(client.post("/vapi/webhook", json=vapi_tool_call("book_appointment", args), headers=SECRET))
    assert again["status"] == "slot_unavailable"
    assert len(client.get(f"/patients/{patient['patient_id']}/appointments").json()["data"]) == 1


def test_end_of_call_report_links_transcript(client):
    saved = _tool_result(client.post("/vapi/webhook", json=vapi_tool_call("register_patient", VALID_PATIENT), headers=SECRET))
    report = {
        "message": {
            "type": "end-of-call-report",
            "call": {"id": "call-1", "customer": {"number": "+15125550142"}},
            "artifact": {"transcript": "AI: Hello\nUser: Hi"},
            "analysis": {"summary": "Registered Jane Doe."},
            "endedReason": "assistant-ended-call",
            "durationSeconds": 95.2,
        }
    }
    assert client.post("/vapi/webhook", json=report, headers=SECRET).status_code == 200
    calls = client.get(f"/patients/{saved['patient_id']}/calls").json()["data"]
    assert calls[0]["summary"] == "Registered Jane Doe."
    assert calls[0]["transcript"].startswith("AI: Hello")
