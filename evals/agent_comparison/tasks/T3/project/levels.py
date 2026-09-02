VALID_LEVELS = frozenset({"debug", "info", "warning", "error"})


def is_valid_level(value: str) -> bool:
    return value in VALID_LEVELS
