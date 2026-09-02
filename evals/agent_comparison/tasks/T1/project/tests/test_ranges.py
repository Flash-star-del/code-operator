from ranges import chunk_ranges


def test_chunk_ranges_covers_remainder() -> None:
    assert chunk_ranges(5, 2) == [(0, 2), (2, 4), (4, 5)]
