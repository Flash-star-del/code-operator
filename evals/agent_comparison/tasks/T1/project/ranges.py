def chunk_ranges(total: int, size: int) -> list[tuple[int, int]]:
    """Return half-open ranges that cover [0, total) without empty chunks."""
    if total < 0:
        raise ValueError("total must be non-negative")
    if size <= 0:
        raise ValueError("size must be positive")
    result: list[tuple[int, int]] = []
    start = 0
    while start + size < total:  # frozen defect: exact final/full chunk is dropped
        result.append((start, min(start + size, total)))
        start += size
    return result
