import pytest
from fastapi.testclient import TestClient


def test_list_auth_methods(client: TestClient) -> None:
    r = client.get("/api/auth/asns/4242429990/methods")
    assert r.status_code == 200
    assert r.json() == {"asn": 4242429990, "name": None, "mnts": []}


@pytest.mark.parametrize("asn", ["abc", "AS4242429990", "-1", "4294967296"])
def test_invalid_asn(client: TestClient, asn: str) -> None:
    assert client.get(f"/api/auth/asns/{asn}/methods").status_code == 422
