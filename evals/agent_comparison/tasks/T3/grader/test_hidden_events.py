import pytest

from events import format_event
from levels import VALID_LEVELS, is_valid_level, normalize_level


@pytest.mark.parametrize("level", sorted(VALID_LEVELS))
def test_normalize_level_accepts_all_supported_levels(level: str) -> None:
    assert normalize_level(f"  {level.upper()}  ") == level
    assert format_event("ok", f" {level.upper()} ") == f"[{level.upper()}] ok"


@pytest.mark.parametrize("value", ["", "   ", "trace"])
def test_normalize_level_rejects_empty_or_unknown(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_level(value)


@pytest.mark.parametrize("value", [None, 1, object()])
def test_normalize_level_rejects_non_strings(value: object) -> None:
    with pytest.raises(TypeError):
        normalize_level(value)  # type: ignore[arg-type]


def test_is_valid_level_remains_backward_compatible() -> None:
    assert is_valid_level("info")
    assert not is_valid_level(" Info ")
    assert not is_valid_level("trace")
