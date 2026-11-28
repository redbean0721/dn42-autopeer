from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from app.auth import methods
from app.auth.methods import MethodType
from app.db import SessionDep

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthMethod(BaseModel):
    type: MethodType
    value: str
    key: str | None = None


class MntAuthMethods(BaseModel):
    mnt: str
    methods: list[AuthMethod]


class AuthMethodsResponse(BaseModel):
    asn: int
    name: str | None
    mnts: list[MntAuthMethods]


@router.get("/asns/{asn}/methods")
async def list_auth_methods(
    session: SessionDep,
    asn: Annotated[int, Path(ge=0, le=4294967295)],
) -> AuthMethodsResponse:
    result = await methods.list_methods(session, asn)
    if result is None:
        raise HTTPException(status_code=404, detail="ASN not found")
    return AuthMethodsResponse(
        asn=result.asn,
        name=result.name,
        mnts=[
            MntAuthMethods(
                mnt=g.mnt,
                methods=[AuthMethod(type=m.type, value=m.value, key=m.key) for m in g.methods],
            )
            for g in result.mnts
        ],
    )
