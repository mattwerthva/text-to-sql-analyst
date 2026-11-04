from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "app_name" in body


def test_echo_roundtrips():
    resp = client.post("/api/echo", json={"message": "hi"})
    assert resp.status_code == 200
    assert resp.json() == {"echo": "hi"}
