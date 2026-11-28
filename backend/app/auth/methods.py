import base64
import binascii
import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.registry.tables import (
    aut_num,
    contact_email,
    key_cert,
    mntner_auth,
    object_,
    object_contact,
)

MethodType = Literal["pgp", "ssh", "email"]

_PGP_FINGERPRINT_RE = re.compile(r"[0-9A-F]{40}|[0-9A-F]{64}")
_SSH_PREFIXES = ("ssh-", "ecdsa-", "sk-")
_DN42_EMAIL_SUFFIX = ".dn42"


@dataclass(frozen=True, slots=True)
class Method:
    type: MethodType
    value: str
    key: str | None = None


@dataclass(slots=True)
class MntMethods:
    mnt: str
    methods: list[Method] = field(default_factory=list)

    def add(self, method: Method) -> None:
        if method not in self.methods:
            self.methods.append(method)


@dataclass(frozen=True, slots=True)
class AsnMethods:
    asn: int
    name: str | None
    mnts: list[MntMethods]


def ssh_fingerprint(blob: str) -> str | None:
    try:
        data = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data:
        return None
    digest = base64.b64encode(hashlib.sha256(data).digest()).decode()
    return "SHA256:" + digest.rstrip("=")


def _auth_method(method: str, value: str, raw: str, cert_fp: str | None) -> Method | None:
    if method == "pgp-fingerprint":
        fp = value
    elif method == "pgp-key-cert":
        fp = cert_fp or ""
    elif method.startswith(_SSH_PREFIXES):
        ssh_fp = ssh_fingerprint(value)
        return Method("ssh", ssh_fp, raw) if ssh_fp else None
    else:
        return None
    return Method("pgp", fp) if _PGP_FINGERPRINT_RE.fullmatch(fp) else None


def _email_method(email: str) -> Method | None:
    email = email.strip()
    dn42 = email.lower().endswith(_DN42_EMAIL_SUFFIX)
    try:
        result = validate_email(email, check_deliverability=False, globally_deliverable=not dn42)
    except EmailNotValidError:
        return None
    return Method("email", result.ascii_email or result.normalized)


async def list_methods(session: AsyncSession, asn: int) -> AsnMethods | None:
    aut = (
        await session.execute(
            select(aut_num.c.as_name, object_.c.attrs["mnt-by"])
            .join(object_, and_(object_.c.type == "aut-num", object_.c.key == aut_num.c.key))
            .where(aut_num.c.asn == asn)
            .limit(1)
        )
    ).first()
    if aut is None:
        return None
    name, mnt_by = aut

    groups = {mnt: MntMethods(mnt) for mnt in dict.fromkeys(m.upper() for m in mnt_by or [])}

    auths = await session.execute(
        select(
            mntner_auth.c.mnt,
            mntner_auth.c.method,
            mntner_auth.c.value,
            mntner_auth.c.raw,
            key_cert.c.fingerprint,
        )
        .outerjoin(
            key_cert,
            and_(mntner_auth.c.method == "pgp-key-cert", key_cert.c.key == mntner_auth.c.value),
        )
        .where(mntner_auth.c.mnt.in_(list(groups)))
        .order_by(mntner_auth.c.mnt, mntner_auth.c.idx)
    )
    for mnt, method, value, raw, cert_fp in auths:
        m = _auth_method(method, value, raw, cert_fp)
        if m is not None:
            groups[mnt].add(m)

    contact_key = func.upper(object_contact.c.key)
    emails = await session.execute(
        select(contact_key, contact_email.c.email)
        .join(contact_email, contact_email.c.handle == object_contact.c.handle)
        .where(object_contact.c.type == "mntner", contact_key.in_(list(groups)))
        .order_by(object_contact.c.kind, object_contact.c.handle, contact_email.c.email)
    )
    for mnt, email in emails:
        m = _email_method(email)
        if m is not None:
            groups[mnt].add(m)

    return AsnMethods(asn=asn, name=name, mnts=list(groups.values()))
