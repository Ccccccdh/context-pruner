def retry_delay(attempt: int, base: int = 2, cap: int = 30) -> int:
    """Return capped exponential backoff."""
    return max(base * (2 ** attempt), cap)
