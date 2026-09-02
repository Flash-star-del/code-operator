import pytest

from fields import split_fields


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("a,,b", ["a", "", "b"]),
        (",lead", ["", "lead"]),
        ("trail,", ["trail", ""]),
        (" a , b ", ["a", "b"]),
        ("single", ["single"]),
        ("", []),
        ("   ", []),
    ],
)
def test_split_fields_expected_output(line: str, expected: list[str]) -> None:
    assert split_fields(line) == expected


def test_split_fields_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="line must be a string"):
        split_fields(None)
