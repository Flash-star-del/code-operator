from fields import split_fields


def test_split_fields_keeps_interior_empty_fields() -> None:
    assert split_fields("a,,b") == ["a", "", "b"]
