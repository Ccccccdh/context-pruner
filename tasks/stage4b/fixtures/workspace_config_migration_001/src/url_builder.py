def service_url(host: str, port: int) -> str:
    """Build the public service endpoint."""
    return f"http://{host}:{port}"
