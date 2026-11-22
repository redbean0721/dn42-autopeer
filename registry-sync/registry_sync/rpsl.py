import re
from dataclasses import dataclass

_ATTR_RE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")


class RpslError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RpslObject:
    type: str
    key: str
    attrs: tuple[tuple[str, str], ...]

    def get(self, name: str) -> str | None:
        for k, v in self.attrs:
            if k == name:
                return v
        return None

    def get_all(self, name: str) -> list[str]:
        return [v for k, v in self.attrs if k == name]

    def as_dict(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for k, v in self.attrs:
            out.setdefault(k, []).append(v)
        return out


def parse_attrs(text: str) -> list[tuple[str, str]]:
    attrs: list[tuple[str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.startswith(("#", "%")):
            continue
        if line[0] in " \t+":
            if not attrs:
                raise RpslError(f"line {lineno}: continuation line before any attribute")
            cont = line[1:].strip() if line[0] == "+" else line.strip()
            key, value = attrs[-1]
            attrs[-1] = (key, f"{value}\n{cont}")
            continue
        m = _ATTR_RE.match(line)
        if m is None:
            raise RpslError(f"line {lineno}: not an attribute: {line[:60]!r}")
        attrs.append((m.group(1).lower(), m.group(2).strip()))
    return attrs


def parse_object(obj_type: str, key: str, text: str) -> RpslObject:
    attrs = parse_attrs(text)
    if not attrs:
        raise RpslError("empty object")
    return RpslObject(type=obj_type, key=key, attrs=tuple(attrs))
