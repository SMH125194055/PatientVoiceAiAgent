"""Vercel entrypoint.

vercel.json rewrites every URL to this one function and passes the original
path in a `__path` query parameter (e.g. /health -> /api/index?__path=health).
The small ASGI wrapper below restores the real path before FastAPI routes the
request, so the same app works identically on Vercel and locally.
"""
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app as fastapi_app  # noqa: E402

PREFIX = "/api/index"


async def app(scope, receive, send):
    if scope["type"] == "http":
        params = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
        original = [value for key, value in params if key == "__path"]
        if original:
            path = "/" + original[0].lstrip("/")
            query = urlencode([(k, v) for k, v in params if k != "__path"])
            scope = {**scope, "path": path, "raw_path": path.encode(), "query_string": query.encode()}
        elif scope.get("path", "").startswith(PREFIX):
            path = scope["path"][len(PREFIX):] or "/"
            scope = {**scope, "path": path, "raw_path": path.encode()}
    await fastapi_app(scope, receive, send)