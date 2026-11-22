import ipaddress
import json
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .rpsl import RpslError, RpslObject, parse_object

log = logging.getLogger(__name__)

_ASN_RE = re.compile(r"^AS(\d+)$", re.IGNORECASE)
_SSH_PREFIXES = ("ssh-", "ecdsa-", "sk-")
CONTACT_KINDS = ("admin-c", "tech-c")


@dataclass(slots=True)
class Snapshot:
    objects: list[tuple[str, str, str, str]] = field(default_factory=list)
    object_mnt: list[tuple[str, str, str]] = field(default_factory=list)
    object_contact: list[tuple[str, str, str, str]] = field(default_factory=list)
    aut_num: list[tuple[int, str, str | None]] = field(default_factory=list)
    mntner_auth: list[tuple[str, int, str, str, str]] = field(default_factory=list)
    key_cert: list[tuple[str, str | None, str | None, str | None, str]] = field(
        default_factory=list
    )
    contact_email: list[tuple[str, str, str]] = field(default_factory=list)
    inetnum: list[tuple[str, str, str, str | None, str | None]] = field(default_factory=list)
    route: list[tuple[str, int, str, str, int | None]] = field(default_factory=list)
    errors: int = 0

    def add(self, obj: RpslObject, raw: str) -> None:
        self.objects.append((obj.type, obj.key, json.dumps(obj.as_dict()), raw))
        for mnt in obj.get_all("mnt-by"):
            self.object_mnt.append((obj.type, obj.key, mnt.upper()))
        for kind in CONTACT_KINDS:
            for handle in obj.get_all(kind):
                self.object_contact.append((obj.type, obj.key, kind, handle.upper()))

        handler = _HANDLERS.get(obj.type)
        if handler is not None:
            handler(self, obj)


def normalize_fingerprint(value: str) -> str:
    fp = "".join(value.split()).upper()
    return fp.removeprefix("0X")


def parse_auth(value: str) -> tuple[str, str]:
    tokens = value.split()
    if not tokens:
        return ("", "")
    method = tokens[0]
    if method.upper().startswith("PGPKEY-"):
        return ("pgp-key-cert", method.upper())
    if method.lower() == "pgp-fingerprint":
        return ("pgp-fingerprint", normalize_fingerprint("".join(tokens[1:])))
    if method.startswith(_SSH_PREFIXES):
        return (method, tokens[1] if len(tokens) > 1 else "")
    return (method, " ".join(tokens[1:]))


def _network(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_network(value.strip(), strict=True))
    except ValueError:
        return None


def _aut_num(snap: Snapshot, obj: RpslObject) -> None:
    m = _ASN_RE.match(obj.key)
    if m is None:
        log.warning("aut-num/%s: key is not an ASN", obj.key)
        return
    snap.aut_num.append((int(m.group(1)), obj.key, obj.get("as-name")))


def _mntner(snap: Snapshot, obj: RpslObject) -> None:
    for idx, value in enumerate(obj.get_all("auth")):
        method, normalized = parse_auth(value)
        if method:
            snap.mntner_auth.append((obj.key.upper(), idx, method, normalized, value))


def _key_cert(snap: Snapshot, obj: RpslObject) -> None:
    fp = obj.get("fingerpr")
    certif = "\n".join(obj.get_all("certif"))
    snap.key_cert.append(
        (
            obj.key.upper(),
            obj.get("method"),
            normalize_fingerprint(fp) if fp else None,
            obj.get("owner"),
            certif,
        )
    )


def _contact(snap: Snapshot, obj: RpslObject) -> None:
    handle = (obj.get("nic-hdl") or obj.key).upper()
    for email in obj.get_all("e-mail"):
        email = email.strip()
        if email:
            snap.contact_email.append((obj.type, handle, email))


def _inetnum(snap: Snapshot, obj: RpslObject) -> None:
    prefix = _network(obj.get("cidr"))
    if prefix is None:
        log.warning("%s/%s: missing or invalid cidr", obj.type, obj.key)
        return
    snap.inetnum.append((prefix, obj.type, obj.key, obj.get("netname"), obj.get("status")))


def _route(snap: Snapshot, obj: RpslObject) -> None:
    prefix = _network(obj.get(obj.type))
    if prefix is None:
        log.warning("%s/%s: invalid prefix", obj.type, obj.key)
        return
    max_length: int | None = None
    raw_ml = obj.get("max-length")
    if raw_ml and raw_ml.strip().isdigit():
        max_length = int(raw_ml)
    seen: set[int] = set()
    for origin in obj.get_all("origin"):
        m = _ASN_RE.match(origin.strip())
        if m is None:
            log.warning("%s/%s: invalid origin %r", obj.type, obj.key, origin)
            continue
        asn = int(m.group(1))
        if asn not in seen:
            seen.add(asn)
            snap.route.append((prefix, asn, obj.type, obj.key, max_length))


_HANDLERS = {
    "aut-num": _aut_num,
    "mntner": _mntner,
    "key-cert": _key_cert,
    "person": _contact,
    "role": _contact,
    "inetnum": _inetnum,
    "inet6num": _inetnum,
    "route": _route,
    "route6": _route,
}


def iter_object_files(data_dir: Path) -> Iterator[tuple[str, Path]]:
    for type_dir in sorted(data_dir.iterdir()):
        if not type_dir.is_dir() or type_dir.name.startswith("."):
            continue
        for path in sorted(type_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                yield type_dir.name, path


def build_snapshot(files: Iterable[tuple[str, Path]]) -> Snapshot:
    snap = Snapshot()
    for obj_type, path in files:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            if "\x00" in raw:
                raise RpslError("contains NUL byte")
            obj = parse_object(obj_type, path.name, raw)
        except (OSError, RpslError) as exc:
            snap.errors += 1
            log.warning("skip %s/%s: %s", obj_type, path.name, exc)
            continue
        snap.add(obj, raw)
    return snap


def load_registry(repo_dir: Path) -> Snapshot:
    data_dir = repo_dir / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(f"{data_dir} is not a directory")
    return build_snapshot(iter_object_files(data_dir))
