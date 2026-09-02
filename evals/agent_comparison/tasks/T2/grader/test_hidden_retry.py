import pytest

from retry import retry_delay


@pytest.mark.parametrize("attempt", range(1, 8))
def test_retry_delay_uses_one_based_attempts(attempt: int) -> None:
    assert retry_delay(attempt, base=0.25, cap=100.0) == min(100.0, 0.25 * (2 ** (attempt - 1)))


def test_retry_delay_uses_non_default_cap() -> None:
    assert retry_delay(4, base=1.5, cap=5.0) == 5.0


@pytest.mark.parametrize(
    ("attempt", "base", "cap"),
    [(0, 0.5, 8.0), (-1, 0.5, 8.0), (1, 0.0, 8.0), (1, -1.0, 8.0), (1, 0.5, 0.0)],
)
def test_retry_delay_rejects_invalid_values(attempt: int, base: float, cap: float) -> None:
    with pytest.raises(ValueError):
        retry_delay(attempt, base=base, cap=cap)
