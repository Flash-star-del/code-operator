import pytest

from ranges import chunk_ranges


def _covered(ranges: list[tuple[int, int]], total: int) -> list[int]:
    return [point for start, end in ranges for point in range(start, end)]


@pytest.mark.parametrize(
    ("total", "size"),
    [(0, 3), (2, 5), (5, 5), (6, 2), (7, 3), (8, 3), (9, 3)],
)
def test_chunk_ranges_covers_each_boundary(total: int, size: int) -> None:
    result = chunk_ranges(total, size)
    assert _covered(result, total) == list(range(total))
    assert all(start < end <= total for start, end in result)
    assert all(left[1] == right[0] for left, right in zip(result, result[1:]))


def test_chunk_ranges_rejects_negative_total() -> None:
    with pytest.raises(ValueError, match="total must be non-negative"):
        chunk_ranges(-1, 2)


def test_chunk_ranges_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="size must be positive"):
        chunk_ranges(2, 0)
