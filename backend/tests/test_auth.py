from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.auth.methods import ssh_fingerprint
from app.registry.tables import (
    aut_num,
    contact_email,
    key_cert,
    mntner_auth,
    object_,
    object_contact,
)

SSH_BLOB = "AAAAC3NzaC1lZDI1NTE5AAAAILcMqzMqJX7Zh9bZ5yQQ92QDYL2KqnT46X2lD/LSIWTR"
SSH_KEY = f"ssh-ed25519 {SSH_BLOB} test@example.com"
SSH_FP = "SHA256:/1RRrElmfs1G6R0VV7Pv/EXvOXQeRoFAmk+avbEYbsM"
FP_A = "A" * 40
FP_CERT = "C" * 40


@pytest.fixture
def registry_data(registry_db: Engine) -> None:
    with registry_db.begin() as conn:
        conn.execute(
            aut_num.insert(),
            [
                {"asn": 4242429990, "key": "AS4242429990", "as_name": "AS4242429990"},
                {"asn": 4242429991, "key": "AS4242429991", "as_name": None},
            ],
        )
        conn.execute(
            object_.insert(),
            [
                {
                    "type": "aut-num",
                    "key": "AS4242429990",
                    "attrs": {
                        "mnt-by": [
                            "as4242429990-mnt",
                            "AS4242429991-MNT",
                            "EMPTY-MNT",
                            "AS4242429990-MNT",
                        ]
                    },
                },
                {"type": "aut-num", "key": "AS4242429991", "attrs": {"mnt-by": ["EMPTY-MNT"]}},
            ],
        )
        conn.execute(
            mntner_auth.insert(),
            [
                {"mnt": m, "idx": i, "method": method, "value": value, "raw": raw}
                for m, i, method, value, raw in [
                    ("AS4242429990-MNT", 0, "pgp-fingerprint", FP_A, f"pgp-fingerprint {FP_A}"),
                    ("AS4242429990-MNT", 1, "ssh-ed25519", SSH_BLOB, SSH_KEY),
                    ("AS4242429990-MNT", 2, "pgp-key-cert", "PGPKEY-CCCCCCCC", "PGPKEY-CCCCCCCC"),
                    ("AS4242429990-MNT", 3, "pgp-key-cert", "PGPKEY-MISSING", "PGPKEY-MISSING"),
                    ("AS4242429990-MNT", 4, "ssh-rsa", "not base64!", "ssh-rsa not base64!"),
                    ("AS4242429990-MNT", 5, "pgp-fingerprint", "XYZ", "pgp-fingerprint XYZ"),
                    ("AS4242429990-MNT", 6, "mail", "x@example.com", "mail x@example.com"),
                    ("AS4242429990-MNT", 7, "pgp-fingerprint", FP_A, f"pgp-fingerprint {FP_A}"),
                    ("AS4242429991-MNT", 0, "pgp-fingerprint", FP_A, f"pgp-fingerprint {FP_A}"),
                ]
            ],
        )
        conn.execute(
            key_cert.insert(),
            [{"key": "PGPKEY-CCCCCCCC", "fingerprint": FP_CERT}],
        )
        conn.execute(
            object_contact.insert(),
            [
                {"type": t, "key": k, "kind": kind, "handle": h}
                for t, k, kind, h in [
                    ("mntner", "AS4242429990-MNT", "admin-c", "ALICE-DN42"),
                    ("mntner", "AS4242429990-MNT", "tech-c", "ALICE-DN42"),
                    ("mntner", "AS4242429990-MNT", "tech-c", "NOEMAIL-DN42"),
                    ("mntner", "AS4242429990-MNT", "tech-c", "CASE1-DN42"),
                    ("mntner", "AS4242429990-MNT", "tech-c", "CASE2-DN42"),
                    ("mntner", "AS4242429990-MNT", "tech-c", "INVALID-DN42"),
                    ("mntner", "AS4242429990-MNT", "tech-c", "DN42-DN42"),
                    ("mntner", "AS4242429991-MNT", "admin-c", "ALICE-DN42"),
                    ("aut-num", "AS4242429990", "admin-c", "BOB-DN42"),
                ]
            ],
        )
        conn.execute(
            contact_email.insert(),
            [
                {"type": "person", "handle": "ALICE-DN42", "email": "alice@example.com"},
                {"type": "role", "handle": "BOB-DN42", "email": "bob@example.com"},
                {"type": "person", "handle": "CASE1-DN42", "email": " alice@EXAMPLE.COM "},
                {"type": "person", "handle": "CASE2-DN42", "email": "Alice@example.com"},
                {"type": "person", "handle": "DN42-DN42", "email": "noc@example.dn42"},
            ]
            + [
                {"type": "person", "handle": "INVALID-DN42", "email": e}
                for e in [
                    "alice at example.com",
                    "alice",
                    "alice@example",
                    "alice@1.2.3.4",
                    "alice@localhost",
                    "alice@dn42",
                    "alice@@example.com",
                    "alice@example.",
                    "al ice@example.com",
                    "alice..b@example.com",
                    f"{'a' * 250}@example.com",
                ]
            ],
        )


def methods(client: TestClient, asn: int) -> dict[str, Any]:
    r = client.get(f"/api/auth/asns/{asn}/methods")
    assert r.status_code == 200
    data: dict[str, Any] = r.json()
    return data


@pytest.mark.usefixtures("registry_data")
def test_list_auth_methods(db_client: TestClient) -> None:
    assert methods(db_client, 4242429990) == {
        "asn": 4242429990,
        "name": "AS4242429990",
        "mnts": [
            {
                "mnt": "AS4242429990-MNT",
                "methods": [
                    {"type": "pgp", "value": FP_A, "key": None},
                    {"type": "ssh", "value": SSH_FP, "key": SSH_KEY},
                    {"type": "pgp", "value": FP_CERT, "key": None},
                    {"type": "email", "value": "alice@example.com", "key": None},
                    {"type": "email", "value": "Alice@example.com", "key": None},
                    {"type": "email", "value": "noc@example.dn42", "key": None},
                ],
            },
            {
                "mnt": "AS4242429991-MNT",
                "methods": [
                    {"type": "pgp", "value": FP_A, "key": None},
                    {"type": "email", "value": "alice@example.com", "key": None},
                ],
            },
            {"mnt": "EMPTY-MNT", "methods": []},
        ],
    }


@pytest.mark.usefixtures("registry_data")
def test_no_usable_methods(db_client: TestClient) -> None:
    assert methods(db_client, 4242429991) == {
        "asn": 4242429991,
        "name": None,
        "mnts": [{"mnt": "EMPTY-MNT", "methods": []}],
    }


@pytest.mark.usefixtures("registry_data")
def test_asn_not_found(db_client: TestClient) -> None:
    r = db_client.get("/api/auth/asns/4242429999/methods")
    assert r.status_code == 404


@pytest.mark.parametrize("asn", ["abc", "AS4242429990", "-1", "4294967296"])
def test_invalid_asn(client: TestClient, asn: str) -> None:
    assert client.get(f"/api/auth/asns/{asn}/methods").status_code == 422


def test_ssh_fingerprint() -> None:
    assert ssh_fingerprint(SSH_BLOB) == SSH_FP
    assert ssh_fingerprint("not base64!") is None
    assert ssh_fingerprint("") is None
