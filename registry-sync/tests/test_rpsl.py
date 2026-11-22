import pytest

from registry_sync.rpsl import RpslError, parse_attrs, parse_object


def test_basic_attrs_are_lowercased_and_ordered() -> None:
    text = "mntner:  FOO-MNT\nAdmin-C: FOO-DN42\nauth:    a\nauth:    b\n"
    assert parse_attrs(text) == [
        ("mntner", "FOO-MNT"),
        ("admin-c", "FOO-DN42"),
        ("auth", "a"),
        ("auth", "b"),
    ]


def test_continuation_lines_whitespace_and_plus() -> None:
    text = (
        "certif:             -----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        "+\n"
        "                    mQINBFex\n"
        "                    =abcd\n"
        "remarks: x\n"
    )
    attrs = parse_attrs(text)
    assert attrs[0] == ("certif", "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQINBFex\n=abcd")
    assert attrs[1] == ("remarks", "x")


def test_empty_value_and_comments() -> None:
    assert parse_attrs("# c\n% c\ncertif:\nremarks:   \n") == [("certif", ""), ("remarks", "")]


def test_continuation_before_attribute_fails() -> None:
    with pytest.raises(RpslError):
        parse_attrs("  orphan\n")


def test_garbage_line_fails() -> None:
    with pytest.raises(RpslError):
        parse_attrs("mntner: X\nthis is not rpsl\n")


def test_object_helpers() -> None:
    obj = parse_object("mntner", "FOO-MNT", "mntner: FOO-MNT\nauth: a\nauth: b\n")
    assert obj.get("auth") == "a"
    assert obj.get("missing") is None
    assert obj.get_all("auth") == ["a", "b"]
    assert obj.as_dict() == {"mntner": ["FOO-MNT"], "auth": ["a", "b"]}


def test_empty_object_fails() -> None:
    with pytest.raises(RpslError):
        parse_object("person", "X", "\n# only comment\n")
