import pytest

from retry import retry_delay


def test_retry_delay_reaches_cap() -> None:
    assert retry_delay(10) == 8.0


@pytest.mark.parametrize("attempt", [0, -1])
def test_retry_delay_rejects_invalid_attempt(attempt: int) -> None:
    with pytest.raises(ValueError, match="attempt"):
        retry_delay(attempt)
