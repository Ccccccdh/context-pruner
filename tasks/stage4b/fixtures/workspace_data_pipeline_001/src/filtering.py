def active_records(rows: list[dict]) -> list[dict]:
    """Keep active records only."""
    return [row for row in rows if row.get("active") is not False]
