"""CRUD + tool-run accept on /investigations."""

from __future__ import annotations

import uuid


def test_create_get_list_investigation(client) -> None:
    body = {
        "subject": {"kind": "username", "value": "linustorvalds"},
        "investigator_handle": "tomas",
        "notes": "smoke",
    }
    r = client.post("/investigations", json=body)
    assert r.status_code == 201
    inv = r.json()
    inv_id = inv["id"]
    assert inv["subject"]["kind"] == "username"
    assert inv["investigator_handle"] == "tomas"

    # GET
    r2 = client.get(f"/investigations/{inv_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == inv_id

    # LIST
    r3 = client.get("/investigations")
    assert r3.status_code == 200
    assert any(i["id"] == inv_id for i in r3.json())


def test_get_unknown_404(client) -> None:
    r = client.get(f"/investigations/{uuid.uuid4()}")
    assert r.status_code == 404


def test_run_tool_unknown_investigation_404(client) -> None:
    r = client.post(
        f"/investigations/{uuid.uuid4()}/run",
        json={"adapter_id": "maigret", "payload": {}},
    )
    assert r.status_code == 404


def test_run_tool_accept(client) -> None:
    inv = client.post(
        "/investigations",
        json={
            "subject": {"kind": "username", "value": "alice"},
            "investigator_handle": "t",
        },
    ).json()
    r = client.post(
        f"/investigations/{inv['id']}/run",
        json={"adapter_id": "maigret", "payload": {"handle": "alice"}},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["investigation_id"] == inv["id"]
    assert body["adapter_id"] == "maigret"


def test_invalid_subject_kind_rejected(client) -> None:
    """Pydantic v2 model validation."""
    r = client.post(
        "/investigations",
        json={"subject": {"kind": "not-a-real-kind", "value": "x"}, "investigator_handle": "t"},
    )
    assert r.status_code == 422
