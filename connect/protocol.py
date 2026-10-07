"""Narrow protocol shared by the public relay and the local connector.

This is intentionally not an arbitrary HTTP proxy. A phone may perform only
the normal current-device chat operations, never account/credential management.
"""
import re
from urllib.parse import parse_qsl, urlsplit

MAX_BODY = 20 * 1024 * 1024
MAX_FRAME = ((MAX_BODY + 2) // 3) * 4 + 16384
_UUID = r"[0-9a-f-]{36}"
_THREAD = re.compile(r"^/api/sessions/(" + _UUID + r")(?:/([a-z-]+))?$")
_FILE = re.compile(r"^/api/sessions/" + _UUID + r"/files/[a-f0-9]{64}$")
_IMAGE = re.compile(r"^/api/sessions/" + _UUID + r"/desktop-images/[a-f0-9]{64}$")
_UPLOAD = re.compile(r"^/api/sessions/" + _UUID + r"/uploads/" + _UUID + r"/(preview|thumb)$")
_GOAL = re.compile(r"^/api/sessions/" + _UUID + r"/goal/(cancel|edit|status)$")
_GET_ACTIONS = {None, "catalog", "poll", "timeline", "detail", "changes", "notifications"}
_POST_ACTIONS = {"send", "stop", "history", "respond", "reconnect", "queue",
                 "settings", "uploads", "message-action", "rename", "notifications"}
_QUERY = {
    None: {"host"}, "catalog": {"host", "refresh", "kind", "q", "limit", "offset", "id"},
    "poll": {"host", "after"},
    "timeline": {"host", "limit", "before"}, "detail": {"host", "key", "offset", "version"},
    "changes": {"host", "after", "epoch", "start"}, "uploads": {"host", "id", "name"},
    "preview": {"host", "variant"}, "thumb": {"host", "width", "height"},
}
_MIME = re.compile(r"^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+(?:;\s*charset=[a-zA-Z0-9_-]+)?$")


def validate_request(method, path, body_size, content_type):
    """Reject unsafe routes, query shapes, methods, framing and oversized data."""
    if method not in {"GET", "POST"}:
        raise ValueError("method is not permitted")
    if not isinstance(path, str) or len(path) > 8192 or any(ord(c) < 32 for c in path):
        raise ValueError("invalid request path")
    parsed = urlsplit(path)
    if (parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith("/api/")
            or "%" in parsed.path or "\\" in path or "//" in parsed.path):
        raise ValueError("relative allowlisted API path required")
    if not isinstance(body_size, int) or isinstance(body_size, bool) or not 0 <= body_size <= MAX_BODY:
        raise ValueError("request body exceeds limit")
    if not isinstance(content_type, str) or not _MIME.fullmatch(content_type):
        raise ValueError("invalid content type")
    if method == "GET" and body_size:
        raise ValueError("GET body is not permitted")
    route, allowed_query, binary = parsed.path, set(), False
    catalog_query = False
    if route == "/api/sessions":
        allowed_query = {"q", "offset", "archived"} if method == "GET" else set()
    elif route == "/api/projects" and method == "GET":
        pass
    elif route == "/api/activity" and method == "POST":
        pass
    elif method == "GET" and (_FILE.fullmatch(route) or _IMAGE.fullmatch(route)):
        allowed_query = {"host"}
    elif _UPLOAD.fullmatch(route):
        action = _UPLOAD.fullmatch(route)[1]
        if (action, method) not in {("preview", "GET"), ("thumb", "POST")}:
            raise ValueError("upload operation is not permitted")
        binary = action == "thumb"
        allowed_query = _QUERY[action]
    elif _GOAL.fullmatch(route) and method == "POST":
        allowed_query = {"host"}
    else:
        match = _THREAD.fullmatch(route)
        if not match:
            raise ValueError("API route is not permitted")
        action = match[2]
        if action not in (_GET_ACTIONS if method == "GET" else _POST_ACTIONS):
            raise ValueError("API operation is not permitted")
        binary = action == "uploads"
        allowed_query = _QUERY.get(action, {"host"})
        catalog_query = method == "GET" and action == "catalog"
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=14 if catalog_query else 12)
    except ValueError:
        raise ValueError("invalid query") from None
    seen = set()
    catalog_ids = 0
    for key, value in query:
        # Selected Skills are the only repeated query field in the chat API.
        # Keep the gateway's eight-Skill cap and reject duplicate routing fields.
        repeated_id = catalog_query and key == "id"
        if key not in allowed_query or (key in seen and not repeated_id) or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("query field is not permitted")
        if repeated_id:
            catalog_ids += 1
            if catalog_ids > 8:
                raise ValueError("too many selected Skills")
        seen.add(key)
    if method == "POST" and not binary and content_type.split(";", 1)[0].lower() != "application/json":
        raise ValueError("JSON content type required")
    return True


def safe_content_type(value):
    """A device cannot inject headers or executable HTML into the relay origin."""
    if not isinstance(value, str) or not _MIME.fullmatch(value):
        return "application/octet-stream"
    base = value.split(";", 1)[0].lower()
    if base in {"text/html", "application/xhtml+xml", "image/svg+xml", "text/javascript", "application/javascript"}:
        return "application/octet-stream"
    return value
