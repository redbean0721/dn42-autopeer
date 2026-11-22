from pathlib import Path

import pytest

from registry_sync.snapshot import load_registry, normalize_fingerprint, parse_auth


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "pgp-fingerprint 866c 48E0 3252 1AFD 6A81 4EC9 DDB4 BA8F B973 AD79",
            ("pgp-fingerprint", "866C48E032521AFD6A814EC9DDB4BA8FB973AD79"),
        ),
        ("pgp-fingerprint 0xABCD", ("pgp-fingerprint", "ABCD")),
        ("PGPKEY-f164e04f", ("pgp-key-cert", "PGPKEY-F164E04F")),
        ("ssh-ed25519 AAAAC3 user@host", ("ssh-ed25519", "AAAAC3")),
        ("ecdsa-sha2-nistp256 AAAAE2", ("ecdsa-sha2-nistp256", "AAAAE2")),
        ("sk-ssh-ed25519@openssh.com AAAAGn c", ("sk-ssh-ed25519@openssh.com", "AAAAGn")),
        ("ssh-rsa", ("ssh-rsa", "")),
        ("weird thing here", ("weird", "thing here")),
        ("", ("", "")),
    ],
)
def test_parse_auth(value: str, expected: tuple[str, str]) -> None:
    assert parse_auth(value) == expected


def test_normalize_fingerprint() -> None:
    assert normalize_fingerprint(" 20ae 2F31\t0a74 ") == "20AE2F310A74"


def test_load_registry(registry_dir: Path) -> None:
    snap = load_registry(registry_dir)

    assert snap.errors == 1
    assert len(snap.objects) == 11
    assert ("aut-num", "AS4242420001", "FOO-MNT") in snap.object_mnt
    assert ("aut-num", "AS4242420001", "BAR-MNT") in snap.object_mnt
    assert snap.aut_num == [(4242420001, "AS4242420001", "FOO-AS")]

    foo_auth = [(m, v) for mnt, _, m, v, _ in snap.mntner_auth if mnt == "FOO-MNT"]
    assert foo_auth == [
        ("pgp-fingerprint", "866C48E032521AFD6A814EC9DDB4BA8FB973AD79"),
        ("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIEYp"),
        ("pgp-key-cert", "PGPKEY-F164E04F"),
    ]

    [kc] = snap.key_cert
    assert kc[0] == "PGPKEY-F164E04F"
    assert kc[2] == "20AE2F310A74EA7CEC3AE69F8B3B0604F164E04F"
    assert kc[4].startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQINBFex\n")

    assert ("mntner", "FOO-MNT", "tech-c", "FOO-ROLE-DN42") in snap.object_contact
    assert ("person", "FOO-DN42", "noc@example.com") in snap.contact_email
    assert ("role", "FOO-ROLE-DN42", "role@example.com") in snap.contact_email

    assert sorted(r[0] for r in snap.inetnum) == ["172.20.45.0/26", "fd00:1234::/48"]
    assert sorted((r[0], r[1], r[4]) for r in snap.route) == [
        ("172.20.45.0/26", 4242420001, 29),
        ("172.20.45.0/26", 4242420002, 29),
        ("fd00:1234::/48", 4242420001, None),
    ]


def test_invalid_cidr_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / "data" / "inetnum").mkdir(parents=True)
    (root / "data" / "inetnum" / "x").write_text("inetnum: x\ncidr: 10.0.0.1/8\n")
    snap = load_registry(root)
    assert len(snap.objects) == 1
    assert snap.inetnum == []


def test_missing_data_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_registry(tmp_path)


def test_file_with_nul_byte_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / "data" / "person").mkdir(parents=True)
    (root / "data" / "person" / "OK-DN42").write_text("person: ok\n")
    (root / "data" / "person" / "NUL-DN42").write_text("person: bad\x00\n")
    snap = load_registry(root)
    assert [o[1] for o in snap.objects] == ["OK-DN42"]
    assert snap.errors == 1
