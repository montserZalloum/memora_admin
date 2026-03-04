"""Request metadata extraction helpers."""

from starlette.requests import Request


def get_client_ip(request: Request) -> str:
	"""Extract client IP, respecting X-Forwarded-For from nginx."""
	forwarded = request.headers.get("X-Forwarded-For")
	if forwarded:
		return forwarded.split(",")[0].strip()
	return request.client.host if request.client else "unknown"
