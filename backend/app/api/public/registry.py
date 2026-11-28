from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.db import SessionDep
from app.registry import queries

router = APIRouter(prefix="/registry", tags=["registry"])


class AsnSearchParams(BaseModel):
    q: str = Field(pattern=r"^[0-9]{1,10}$")
    limit: int = Field(10, ge=1, le=10)


class AsnItem(BaseModel):
    asn: int
    name: str | None
    mnts: list[str]


class AsnSearchResponse(BaseModel):
    items: list[AsnItem]


@router.get("/asns")
async def search_asns(
    session: SessionDep,
    params: Annotated[AsnSearchParams, Query()],
) -> AsnSearchResponse:
    rows = await queries.search_asns(session, params.q, params.limit)
    return AsnSearchResponse(
        items=[AsnItem(asn=r.asn, name=r.name, mnts=r.mnts) for r in rows],
    )
