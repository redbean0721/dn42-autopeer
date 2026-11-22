import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql

from .snapshot import Snapshot

log = logging.getLogger(__name__)

SCHEMA = "registry"
SCHEMA_VERSION = 1
ADVISORY_LOCK_KEY = 0x646E3432_0001  # "dn42" in ASCII + 0001

DDL = f"""
CREATE TABLE {SCHEMA}.meta (
    id             boolean PRIMARY KEY DEFAULT true CHECK (id),
    schema_version integer NOT NULL,
    commit         text,
    commit_time    timestamptz,
    synced_at      timestamptz,
    object_count   integer NOT NULL DEFAULT 0
);

CREATE TABLE {SCHEMA}.object (
    type  text  NOT NULL,
    key   text  NOT NULL,
    attrs jsonb NOT NULL,
    raw   text  NOT NULL,
    PRIMARY KEY (type, key)
);

CREATE TABLE {SCHEMA}.object_mnt (
    type text NOT NULL,
    key  text NOT NULL,
    mnt  text NOT NULL
);
CREATE INDEX ON {SCHEMA}.object_mnt (mnt);
CREATE INDEX ON {SCHEMA}.object_mnt (type, key);

CREATE TABLE {SCHEMA}.object_contact (
    type   text NOT NULL,
    key    text NOT NULL,
    kind   text NOT NULL,
    handle text NOT NULL
);
CREATE INDEX ON {SCHEMA}.object_contact (type, key);
CREATE INDEX ON {SCHEMA}.object_contact (handle);

CREATE TABLE {SCHEMA}.aut_num (
    asn     bigint NOT NULL,
    key     text   NOT NULL,
    as_name text
);
CREATE INDEX ON {SCHEMA}.aut_num (asn);

CREATE TABLE {SCHEMA}.mntner_auth (
    mnt    text    NOT NULL,
    idx    integer NOT NULL,
    method text    NOT NULL,
    value  text    NOT NULL,
    raw    text    NOT NULL
);
CREATE INDEX ON {SCHEMA}.mntner_auth (mnt);

CREATE TABLE {SCHEMA}.key_cert (
    key         text NOT NULL,
    method      text,
    fingerprint text,
    owner       text,
    certif      text NOT NULL
);
CREATE INDEX ON {SCHEMA}.key_cert (key);

CREATE TABLE {SCHEMA}.contact_email (
    type   text NOT NULL,
    handle text NOT NULL,
    email  text NOT NULL
);
CREATE INDEX ON {SCHEMA}.contact_email (handle);

CREATE TABLE {SCHEMA}.inetnum (
    prefix  cidr NOT NULL,
    type    text NOT NULL,
    key     text NOT NULL,
    netname text,
    status  text
);
CREATE INDEX ON {SCHEMA}.inetnum USING gist (prefix inet_ops);

CREATE TABLE {SCHEMA}.route (
    prefix     cidr    NOT NULL,
    origin     bigint  NOT NULL,
    type       text    NOT NULL,
    key        text    NOT NULL,
    max_length integer
);
CREATE INDEX ON {SCHEMA}.route USING gist (prefix inet_ops);
CREATE INDEX ON {SCHEMA}.route (origin);
"""

TABLE_COLUMNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "objects": ("object", ("type", "key", "attrs", "raw")),
    "object_mnt": ("object_mnt", ("type", "key", "mnt")),
    "object_contact": ("object_contact", ("type", "key", "kind", "handle")),
    "aut_num": ("aut_num", ("asn", "key", "as_name")),
    "mntner_auth": ("mntner_auth", ("mnt", "idx", "method", "value", "raw")),
    "key_cert": ("key_cert", ("key", "method", "fingerprint", "owner", "certif")),
    "contact_email": ("contact_email", ("type", "handle", "email")),
    "inetnum": ("inetnum", ("prefix", "type", "key", "netname", "status")),
    "route": ("route", ("prefix", "origin", "type", "key", "max_length")),
}


@dataclass(frozen=True, slots=True)
class SyncState:
    commit: str | None
    commit_time: datetime | None
    synced_at: datetime | None
    object_count: int


def connect(conninfo: str) -> psycopg.Connection[Any]:
    return psycopg.connect(conninfo, connect_timeout=10, application_name="registry-sync")


def _lock(conn: psycopg.Connection[Any]) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))


class SchemaMissingError(RuntimeError):
    pass


def _current_version(conn: psycopg.Connection[Any]) -> int | None:
    exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{SCHEMA}.meta",)).fetchone()
    if not (exists and exists[0]):
        return None
    row = conn.execute(f"SELECT schema_version FROM {SCHEMA}.meta").fetchone()
    return row[0] if row else None


def _drop_tables(conn: psycopg.Connection[Any]) -> None:
    tables = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s", (SCHEMA,)
    ).fetchall()
    for (table,) in tables:
        conn.execute(
            sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA, table))
        )


def _grant_readers(conn: psycopg.Connection[Any], readers: Sequence[str]) -> None:
    for role in readers:
        found = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if found is None:
            log.warning("reader role %r does not exist, skipping grant", role)
            continue
        ident = sql.Identifier(role)
        schema = sql.Identifier(SCHEMA)
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, ident))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(schema, ident))


def ensure_schema(conn: psycopg.Connection[Any], readers: Sequence[str] = ()) -> None:
    with conn.transaction():
        _lock(conn)
        found = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (SCHEMA,)).fetchone()
        if found is None:
            raise SchemaMissingError(
                f"schema {SCHEMA!r} does not exist; it is created by the CNPG Database resource"
            )
        version = _current_version(conn)
        if version != SCHEMA_VERSION:
            if version is not None:
                log.warning("registry schema version %s != %s, rebuilding", version, SCHEMA_VERSION)
            _drop_tables(conn)
            conn.execute(DDL)
            conn.execute(
                f"INSERT INTO {SCHEMA}.meta (schema_version) VALUES (%s)", (SCHEMA_VERSION,)
            )
            log.info("created registry tables v%s", SCHEMA_VERSION)
        _grant_readers(conn, readers)


def get_state(conn: psycopg.Connection[Any]) -> SyncState:
    row = conn.execute(
        f"SELECT commit, commit_time, synced_at, object_count FROM {SCHEMA}.meta"
    ).fetchone()
    conn.commit()
    if row is None:
        return SyncState(None, None, None, 0)
    return SyncState(row[0], row[1], row[2], row[3])


def import_snapshot(
    conn: psycopg.Connection[Any], snap: Snapshot, commit: str, commit_time: datetime
) -> None:
    with conn.transaction():
        _lock(conn)
        for table, _ in TABLE_COLUMNS.values():
            conn.execute(f"DELETE FROM {SCHEMA}.{table}")
        with conn.cursor() as cur:
            for attr, (table, columns) in TABLE_COLUMNS.items():
                rows: list[tuple[Any, ...]] = getattr(snap, attr)
                cols = ", ".join(columns)
                with cur.copy(f"COPY {SCHEMA}.{table} ({cols}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(row)
        conn.execute(
            f"UPDATE {SCHEMA}.meta SET commit = %s, commit_time = %s, synced_at = now(),"
            " object_count = %s",
            (commit, commit_time, len(snap.objects)),
        )


def touch(conn: psycopg.Connection[Any]) -> None:
    with conn.transaction():
        conn.execute(f"UPDATE {SCHEMA}.meta SET synced_at = now()")
