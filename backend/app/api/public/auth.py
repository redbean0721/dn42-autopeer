from typing import Annotated, Literal

from fastapi import APIRouter, Path
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthMethod(BaseModel):
    type: Literal["pgp", "ssh", "email"]
    value: str
    key: str | None = None


class MntAuthMethods(BaseModel):
    mnt: str | None
    methods: list[AuthMethod]


class AuthMethodsResponse(BaseModel):
    asn: int
    name: str | None
    mnts: list[MntAuthMethods]


@router.get("/asns/{asn}/methods")
async def list_auth_methods(
    asn: Annotated[int, Path(ge=0, le=4294967295)],
) -> AuthMethodsResponse:
    return AuthMethodsResponse(asn=asn, name=None, mnts=[])
