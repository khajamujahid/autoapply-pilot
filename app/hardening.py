"""Production middleware: JSON structured logging, request ids, security headers, same-origin (CSRF) checks,
login throttling and friendly error pages."""
import json
import logging
import sys
import time
import uuid
from collections import defaultdict, deque
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

SAFE = {"GET", "HEAD", "OPTIONS"}


class JsonFormatter(logging.Formatter):
    def format(self, r):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + "Z", "level": r.levelname,
             "logger": r.name, "msg": r.getMessage()}
        for k in ("rid", "method", "path", "status", "ms", "uid"):
            if hasattr(r, k):
                d[k] = getattr(r, k)
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d)


def setup_logging(level="INFO", fmt="json"):
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


log = logging.getLogger("http")


class RequestMiddleware(BaseHTTPMiddleware):
    """Request id + access log + security headers + same-origin check for state-changing requests."""

    def __init__(self, app, base_url=""):
        super().__init__(app)
        self.allowed = {urlparse(base_url).netloc} if base_url else set()

    def _same_origin(self, request):
        src = request.headers.get("origin") or request.headers.get("referer")
        if not src:
            return True  # non-browser clients (curl/tests); browsers always send Origin on cross-site POSTs
        host = urlparse(src).netloc
        return host == request.headers.get("host") or host in self.allowed

    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        t = time.perf_counter()
        if request.method not in SAFE and not self._same_origin(request):
            log.warning("blocked cross-site request", extra={"rid": rid, "method": request.method, "path": request.url.path})
            return PlainTextResponse("Cross-site request blocked", 403)
        try:
            resp = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"rid": rid, "method": request.method, "path": request.url.path})
            resp = PlainTextResponse(f"Something went wrong (request id {rid}). It has been logged.", 500)
        resp.headers["X-Request-ID"] = rid
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path != "/healthz":
            log.info("request", extra={"rid": rid, "method": request.method, "path": request.url.path,
                                       "status": resp.status_code, "ms": round((time.perf_counter() - t) * 1000)})
        return resp


class LoginThrottle:
    """At most `limit` failed logins per key (ip+email) per `window` seconds."""

    def __init__(self, limit=8, window=600):
        self.limit, self.window, self.fails = limit, window, defaultdict(deque)

    def _prune(self, key):
        q, now = self.fails[key], time.time()
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key):
        return len(self._prune(key)) >= self.limit

    def fail(self, key):
        self._prune(key).append(time.time())

    def reset(self, key):
        self.fails.pop(key, None)
