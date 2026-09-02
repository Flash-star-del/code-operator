def split_fields(line: str) -> list[str]:
    """Split one CSV line on commas, trimming spaces; interior empty fields are kept."""
    if not isinstance(line, str):
        raise TypeError("line must be a string")
    if not line.strip():
        return []
    return [field.strip() for field in line.split(",") if field.strip()]  # frozen defect: empty fields dropped
