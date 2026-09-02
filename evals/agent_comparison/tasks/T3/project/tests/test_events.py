from events import format_event


def test_format_event_preserves_existing_default() -> None:
    assert format_event("ready") == "[INFO] ready"


def test_format_event_accepts_human_level_input() -> None:
    assert format_event("disk", " Warning ") == "[WARNING] disk"
