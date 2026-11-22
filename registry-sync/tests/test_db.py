from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest

from registry_sync import db
from registry_sync.snapshot import load_registry

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_worker_role_is_not_superuser(conn: psycopg.Connection[Any]) -> None:
    row = conn.execute("SELECT current_user, usesuper FROM pg_user WHERE usename = current_user")
    assert row.fetchone() == ("registry_sync", False)
    conn.rollback()


def test_ensure_schema_is_idempotent_and_rebuilds_on_version_change(
    conn: psycopg.Connection[Any], reader_url: str
) -> None:
    db.ensure_schema(conn, ["autopeer"])
    db.ensure_schema(conn, ["autopeer"])
    assert db.get_state(conn).commit is None

    with conn.transaction():
        conn.execute("UPDATE registry.meta SET schema_version = 0, commit = 'old'")
        conn.execute("CREATE TABLE registry.leftover (x int)")
    db.ensure_schema(conn, ["autopeer"])
    assert db.get_state(conn).commit is None

    row = conn.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'registry'"
    ).fetchone()
    assert row == ("registry_sync",)
    assert conn.execute("SELECT to_regclass('registry.leftover')").fetchone() == (None,)
    conn.commit()

    with psycopg.connect(reader_url) as reader:
        assert reader.execute("SELECT count(*) FROM registry.meta").fetchone() == (1,)


def test_missing_schema_raises(conn: psycopg.Connection[Any], admin_url: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute("DROP SCHEMA registry CASCADE")
    with pytest.raises(db.SchemaMissingError):
        db.ensure_schema(conn)


def test_unknown_reader_role_is_skipped(conn: psycopg.Connection[Any]) -> None:
    db.ensure_schema(conn, ["does_not_exist"])
    assert db.get_state(conn).commit is None


def test_reader_can_only_select(
    conn: psycopg.Connection[Any], reader_url: str, registry_dir: Path
) -> None:
    db.ensure_schema(conn, ["autopeer"])
    db.import_snapshot(conn, load_registry(registry_dir), "abc", T0)

    with psycopg.connect(reader_url, autocommit=True) as reader:
        assert reader.execute("SELECT count(*) FROM registry.object").fetchone() == (11,)
        for stmt in (
            "INSERT INTO registry.aut_num (asn, key) VALUES (1, 'AS1')",
            "DELETE FROM registry.object",
            "UPDATE registry.meta SET commit = 'evil'",
            "CREATE TABLE registry.evil (x int)",
            "DROP TABLE registry.object",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(stmt)


def test_import_and_queries(conn: psycopg.Connection[Any], registry_dir: Path) -> None:
    db.ensure_schema(conn)
    snap = load_registry(registry_dir)
    db.import_snapshot(conn, snap, "abc", T0)

    state = db.get_state(conn)
    assert (state.commit, state.commit_time, state.object_count) == ("abc", T0, 11)

    rows = conn.execute(
        "SELECT m.mnt FROM registry.aut_num n JOIN registry.object_mnt m"
        " ON m.type = 'aut-num' AND m.key = n.key WHERE n.asn = 4242420001 ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in rows] == ["BAR-MNT", "FOO-MNT"]

    rows = conn.execute(
        """
        SELECT DISTINCT e.email FROM registry.object_contact c
        JOIN registry.contact_email e ON e.handle = c.handle
        JOIN registry.object_mnt pm ON pm.type = e.type AND pm.key = c.handle AND pm.mnt = c.key
        WHERE c.type = 'mntner' AND c.key = 'FOO-MNT' ORDER BY 1
        """
    ).fetchall()
    assert [r[0] for r in rows] == ["foo@example.com", "noc@example.com", "role@example.com"]

    row = conn.execute(
        "SELECT key FROM registry.inetnum WHERE prefix >>= %s::inet", ("fd00:1234::1",)
    ).fetchone()
    assert row == ("fd00:1234::_48",)

    attrs = conn.execute(
        "SELECT attrs -> 'auth' FROM registry.object WHERE type = 'mntner' AND key = 'FOO-MNT'"
    ).fetchone()
    assert attrs is not None and len(attrs[0]) == 3
    conn.commit()


def test_reimport_replaces_everything(conn: psycopg.Connection[Any], registry_dir: Path) -> None:
    db.ensure_schema(conn)
    db.import_snapshot(conn, load_registry(registry_dir), "c1", T0)
    (registry_dir / "data" / "aut-num" / "AS4242420001").unlink()
    db.import_snapshot(conn, load_registry(registry_dir), "c2", T0)

    assert db.get_state(conn).commit == "c2"
    count = conn.execute("SELECT count(*) FROM registry.aut_num").fetchone()
    assert count == (0,)
    mnt = conn.execute("SELECT count(*) FROM registry.object_mnt WHERE type = 'aut-num'").fetchone()
    assert mnt == (0,)
    conn.commit()


def test_readers_see_old_data_during_import(
    db_url: str, conn: psycopg.Connection[Any], registry_dir: Path
) -> None:
    db.ensure_schema(conn)
    db.import_snapshot(conn, load_registry(registry_dir), "c1", T0)

    snap = load_registry(registry_dir)
    with psycopg.connect(db_url) as reader:
        with conn.transaction():
            conn.execute("DELETE FROM registry.object")
            row = reader.execute("SELECT commit FROM registry.meta").fetchone()
            assert row == ("c1",)
            reader.execute("SET lock_timeout = '1s'")
            n = reader.execute("SELECT count(*) FROM registry.object").fetchone()
            assert n == (11,)
            reader.rollback()
            raise psycopg.Rollback()
    db.import_snapshot(conn, snap, "c2", T0)
    assert db.get_state(conn).commit == "c2"
