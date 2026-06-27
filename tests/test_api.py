from uuid import uuid4

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import HospitalResult, RowStatus
from app.store import store

BASE = "https://hospital-directory.onrender.com"
client = TestClient(app)


def test_health():
    assert client.get("/").status_code == 200


def test_validate_endpoint_reports_row_errors():
    csv = b"name,address,phone\nA,1 St,555\n,2 St,556\n"
    resp = client.post("/hospitals/bulk/validate", files={"file": ("h.csv", csv, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert data["valid_rows"] == 1
    assert data["invalid_rows"] == 1


def test_bulk_rejects_bad_header():
    csv = b"foo,bar\n1,2\n"
    resp = client.post("/hospitals/bulk", files={"file": ("h.csv", csv, "text/csv")})
    assert resp.status_code == 400


def test_bulk_rejects_too_many_rows():
    body = "name,address\n" + "".join(f"H{i},addr\n" for i in range(25))
    resp = client.post(
        "/hospitals/bulk", files={"file": ("h.csv", body.encode(), "text/csv")}
    )
    assert resp.status_code == 400


@respx.mock
def test_bulk_happy_path():
    respx.post(f"{BASE}/hospitals/").mock(return_value=httpx.Response(200, json={"id": 42}))
    respx.patch(url__regex=rf"{BASE}/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    csv = b"name,address,phone\nGeneral,1 St,555\nMercy,2 St,556\n"
    resp = client.post("/hospitals/bulk", files={"file": ("h.csv", csv, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_hospitals"] == 2
    assert data["failed_hospitals"] == 0
    assert data["batch_activated"] is True
    assert data["hospitals"][0]["status"] == "created_and_activated"


@respx.mock
def test_bulk_status_after_run():
    respx.post(f"{BASE}/hospitals/").mock(return_value=httpx.Response(200, json={"id": 1}))
    respx.patch(url__regex=rf"{BASE}/hospitals/batch/.*/activate").mock(
        return_value=httpx.Response(200)
    )
    csv = b"name,address\nA,1 St\n"
    resp = client.post("/hospitals/bulk", files={"file": ("h.csv", csv, "text/csv")})
    batch_id = resp.json()["batch_id"]
    status = client.get(f"/hospitals/bulk/{batch_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"


def test_ws_streams_terminal_status():
    # Seed a completed batch directly in the store and stream it.
    batch_id = uuid4()
    store.create(batch_id, total=1)
    store.record_result(
        batch_id, HospitalResult(row=1, hospital_id=5, name="A", status=RowStatus.CREATED_AND_ACTIVATED)
    )
    store.set_activated(batch_id, True)
    store.set_status(batch_id, "completed")

    with client.websocket_connect(f"/hospitals/bulk/{batch_id}/ws") as ws:
        frame = ws.receive_json()
    assert frame["status"] == "completed"
    assert frame["processed"] == 1
    assert frame["activated"] is True


def test_ws_unknown_batch_id():
    with client.websocket_connect(f"/hospitals/bulk/{uuid4()}/ws") as ws:
        frame = ws.receive_json()
    assert "error" in frame
