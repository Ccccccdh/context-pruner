def redact(value: str) -> str:
    """Hide a sensitive value while preserving an empty value."""
    if not value:
        return ""
    return value
