from sqlalchemy import BigInteger, Column, Integer, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="registry")

object_ = Table(
    "object",
    metadata,
    Column("type", Text, primary_key=True),
    Column("key", Text, primary_key=True),
    Column("attrs", JSONB, nullable=False),
)

object_contact = Table(
    "object_contact",
    metadata,
    Column("type", Text, nullable=False),
    Column("key", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("handle", Text, nullable=False),
)

aut_num = Table(
    "aut_num",
    metadata,
    Column("asn", BigInteger, nullable=False),
    Column("key", Text, nullable=False),
    Column("as_name", Text),
)

mntner_auth = Table(
    "mntner_auth",
    metadata,
    Column("mnt", Text, nullable=False),
    Column("idx", Integer, nullable=False),
    Column("method", Text, nullable=False),
    Column("value", Text, nullable=False),
    Column("raw", Text, nullable=False),
)

key_cert = Table(
    "key_cert",
    metadata,
    Column("key", Text, nullable=False),
    Column("fingerprint", Text),
)

contact_email = Table(
    "contact_email",
    metadata,
    Column("type", Text, nullable=False),
    Column("handle", Text, nullable=False),
    Column("email", Text, nullable=False),
)
