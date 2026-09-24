"""Restore the original request path on Vercel.

vercel.json rewrites every URL to /api/index and passes the original path in a
`__path` query parameter (/health -> /api/index?__path=health). This ASGI
middleware runs before routing and puts the real path back, so routing works
the same on Vercel and locally, whichever entrypoint Vercel chooses to run.
Locally there is no `__path`, so it does nothing.
"""
from urllib.parse import parse_qsl, urlencode

REWRITE_PREFIX = "/api/index"


def restore_path(scope: dict) -> dict:
    params = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
    original = [value for key, value in params if key == "__path"]
    if original:
        path = "/" + original[0].lstrip("/")
        query = urlencode([(k, v) for k, v in params if k != "__path"])
        return {**scope, "path": path, "raw_path": path.encode(), "query_string": query.encode()}
    if scope.get("path", "").startswith(REWRITE_PREFIX):
        path = scope["path"][len(REWRITE_PREFIX):] or "/"
        return {**scope, "path": path, "raw_path": path.encode()}
    return scope


class VercelPathMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = restore_path(scope)
        await self.app(scope, receive, send)