from dataclasses import dataclass

from sqlalchemy import Text, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.registry.tables import aut_num, object_


@dataclass(frozen=True, slots=True)
class AsnSummary:
    asn: int
    name: str | None
    mnts: list[str]


async def search_asns(session: AsyncSession, prefix: str, limit: int) -> list[AsnSummary]:
    asn_text = cast(aut_num.c.asn, Text)
    stmt = (
        select(aut_num.c.asn, aut_num.c.as_name, object_.c.attrs["mnt-by"])
        .join(object_, (object_.c.type == "aut-num") & (object_.c.key == aut_num.c.key))
        .where(asn_text.startswith(prefix))
        .order_by((asn_text == prefix).desc(), aut_num.c.asn)
        .limit(limit)
    )
    rows = await session.execute(stmt)
    return [
        AsnSummary(asn=asn, name=name, mnts=[m.upper() for m in mnts or []])
        for asn, name, mnts in rows
    ]
