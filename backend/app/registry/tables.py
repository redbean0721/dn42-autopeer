from sqlalchemy import BigInteger, Column, MetaData, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="registry")

object_ = Table(
    "object",
    metadata,
    Column("type", Text, primary_key=True),
    Column("key", Text, primary_key=True),
    Column("attrs", JSONB, nullable=False),
)

aut_num = Table(
    "aut_num",
    metadata,
    Column("asn", BigInteger, nullable=False),
    Column("key", Text, nullable=False),
    Column("as_name", Text),
)
