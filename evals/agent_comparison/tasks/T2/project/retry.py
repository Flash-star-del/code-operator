def retry_delay(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    """Return capped delay; attempt 1 waits base seconds."""
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    if base <= 0 or cap <= 0:
        raise ValueError("base and cap must be positive")
    return min(cap, base * (2 ** attempt))  # frozen defect: exponent is one too high
