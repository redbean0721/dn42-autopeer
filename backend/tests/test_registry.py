from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.registry.tables import aut_num, metadata, object_

AUT_NUMS: list[tuple[int, str | None, list[str]]] = [
    (424242999, "AS424242999", ["AS424242999-MNT"]),
    (4242429990, "AS4242429990", ["AS4242429990-MNT"]),
    (4242429991, "AS4242429991", ["AS4242429991-MNT"]),
    (4242429992, "AS4242429992", ["as4242429992-mnt", "AS4242429990-MNT", "AS4242429991-MNT"]),
    (4242429993, None, ["AS4242429993-MNT"]),
    (4242420001, "AS4242420001", ["AS4242420001-MNT"]),
]


@pytest.fixture
def registry_data(db_url: str) -> Iterator[None]:
    engine = create_engine(db_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS registry CASCADE"))
        conn.execute(text("CREATE SCHEMA registry"))
        metadata.create_all(conn)
        conn.execute(
            aut_num.insert(),
            [{"asn": asn, "key": f"AS{asn}", "as_name": name} for asn, name, _ in AUT_NUMS],
        )
        conn.execute(
            object_.insert(),
            [
                {"type": "aut-num", "key": f"AS{asn}", "attrs": {"mnt-by": mnts}}
                for asn, _, mnts in AUT_NUMS
            ],
        )
    yield
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA registry CASCADE"))
    engine.dispose()


def search(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    r = client.get("/api/registry/asns", params=params)
    assert r.status_code == 200
    items: list[dict[str, Any]] = r.json()["items"]
    return items


@pytest.mark.usefixtures("registry_data")
def test_prefix_match_sorted_by_asn(db_client: TestClient) -> None:
    items = search(db_client, q="42424299")
    assert [i["asn"] for i in items] == [424242999, 4242429990, 4242429991, 4242429992, 4242429993]


@pytest.mark.usefixtures("registry_data")
def test_exact_match_first(db_client: TestClient) -> None:
    items = search(db_client, q="4242429992")
    assert [i["asn"] for i in items] == [4242429992]
    items = search(db_client, q="424242999")
    assert [i["asn"] for i in items][:2] == [424242999, 4242429990]


@pytest.mark.usefixtures("registry_data")
def test_limit(db_client: TestClient) -> None:
    assert len(search(db_client, q="4242429", limit=2)) == 2


@pytest.mark.usefixtures("registry_data")
def test_item_fields(db_client: TestClient) -> None:
    assert search(db_client, q="4242429992") == [
        {
            "asn": 4242429992,
            "name": "AS4242429992",
            "mnts": ["AS4242429992-MNT", "AS4242429990-MNT", "AS4242429991-MNT"],
        }
    ]
    assert search(db_client, q="4242429993")[0]["name"] is None


@pytest.mark.usefixtures("registry_data")
def test_no_match(db_client: TestClient) -> None:
    assert search(db_client, q="999") == []


@pytest.mark.parametrize(
    "q",
    [
        "",
        " 4242420001",
        "4242420001 ",
        "AS4242420001",
        "as4242420001",
        "abc",
        "4242-2020",
        "42424200011",
        "%",
        "4_",
        "４２４",
        "4242\n",
    ],
)
def test_invalid_query(client: TestClient, q: str) -> None:
    assert client.get("/api/registry/asns", params={"q": q}).status_code == 422


def test_requires_q(client: TestClient) -> None:
    assert client.get("/api/registry/asns").status_code == 422


@pytest.mark.parametrize("limit", [0, 11, "x"])
def test_invalid_limit(client: TestClient, limit: int | str) -> None:
    r = client.get("/api/registry/asns", params={"q": "4242", "limit": limit})
    assert r.status_code == 422
