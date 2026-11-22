import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

FIXTURE: dict[str, str] = {
    "mntner/FOO-MNT": """\
mntner:             FOO-MNT
admin-c:            FOO-DN42
tech-c:             OTHER-DN42
tech-c:             FOO-ROLE-DN42
mnt-by:             FOO-MNT
auth:               pgp-fingerprint 866c 48e0 3252 1AFD 6A81 4EC9 DDB4 BA8F B973 AD79
auth:               ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEYp foo@example.com
auth:               PGPKEY-F164E04F
source:             DN42
""",
    "mntner/BAR-MNT": """\
mntner:             BAR-MNT
admin-c:            OTHER-DN42
mnt-by:             BAR-MNT
auth:               ssh-rsa AAAAB3NzaC1yc2E
source:             DN42
""",
    "aut-num/AS4242420001": """\
aut-num:            AS4242420001
as-name:            FOO-AS
admin-c:            FOO-DN42
tech-c:             FOO-DN42
mnt-by:             FOO-MNT
mnt-by:             BAR-MNT
source:             DN42
""",
    "person/FOO-DN42": """\
person:             Foo
e-mail:             foo@example.com
e-mail:             noc@example.com
nic-hdl:            FOO-DN42
mnt-by:             FOO-MNT
source:             DN42
""",
    "person/OTHER-DN42": """\
person:             Other
e-mail:             other@example.net
nic-hdl:            OTHER-DN42
mnt-by:             BAR-MNT
source:             DN42
""",
    "role/FOO-ROLE-DN42": """\
role:               Foo NOC
e-mail:             role@example.com
nic-hdl:            FOO-ROLE-DN42
mnt-by:             FOO-MNT
source:             DN42
""",
    "key-cert/PGPKEY-F164E04F": """\
key-cert:           PGPKEY-F164E04F
method:             PGP
fingerpr:           20AE 2F31 0A74 EA7C EC3A E69F 8B3B 0604 F164 E04F
owner:              foo@example.com
mnt-by:             FOO-MNT
certif:             -----BEGIN PGP PUBLIC KEY BLOCK-----
+
                    mQINBFex
                    -----END PGP PUBLIC KEY BLOCK-----
source:             DN42
""",
    "inetnum/172.20.45.0_26": """\
inetnum:            172.20.45.0 - 172.20.45.63
cidr:               172.20.45.0/26
netname:            FOO-V4
mnt-by:             FOO-MNT
status:             ASSIGNED
source:             DN42
""",
    "inet6num/fd00:1234::_48": """\
inet6num:   fd00:1234:0000:0000:0000:0000:0000:0000 - fd00:1234:0000:ffff:ffff:ffff:ffff:ffff
cidr:               fd00:1234::/48
netname:            FOO-V6
mnt-by:             FOO-MNT
source:             DN42
""",
    "route/172.20.45.0_26": """\
route:              172.20.45.0/26
origin:             AS4242420001
origin:             AS4242420002
origin:             AS4242420001
max-length:         29
mnt-by:             FOO-MNT
source:             DN42
""",
    "route6/fd00:1234::_48": """\
route6:             fd00:1234::/48
origin:             AS4242420001
mnt-by:             FOO-MNT
source:             DN42
""",
    "person/BROKEN-DN42": "this is not rpsl\n",
}


def write_registry(root: Path, files: dict[str, str] = FIXTURE) -> Path:
    for rel, content in files.items():
        path = root / "data" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (root / "data" / "filter.txt").write_text("# not an object\n")
    return root


def git(cwd: Path, *args: str) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def registry_dir(tmp_path: Path) -> Path:
    return write_registry(tmp_path / "registry")


@pytest.fixture
def remote_repo(tmp_path: Path) -> Path:
    repo = write_registry(tmp_path / "remote")
    git(repo, "init", "-q", "-b", "master")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


ROLE_PASSWORD = "test"


def _admin_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


def _ensure_role(conn: psycopg.Connection[Any], role: str) -> None:
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(ROLE_PASSWORD)
            )
        )


@pytest.fixture
def admin_url() -> str:
    return _admin_url()


@pytest.fixture
def db_url(admin_url: str) -> Iterator[str]:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS registry CASCADE")
        _ensure_role(conn, "registry_sync")
        _ensure_role(conn, "autopeer")
        conn.execute("CREATE SCHEMA registry AUTHORIZATION registry_sync")
    yield make_conninfo(admin_url, user="registry_sync", password=ROLE_PASSWORD)


@pytest.fixture
def reader_url(db_url: str, admin_url: str) -> str:
    return make_conninfo(admin_url, user="autopeer", password=ROLE_PASSWORD)


@pytest.fixture
def conn(db_url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(db_url) as c:
        yield c
