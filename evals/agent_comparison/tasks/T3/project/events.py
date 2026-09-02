from levels import is_valid_level


def format_event(message: str, level: str = "info") -> str:
    if not is_valid_level(level):
        raise ValueError(f"unsupported level: {level}")
    return f"[{level.upper()}] {message}"
