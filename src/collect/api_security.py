"""API-Härtung — Auth, Rate-Limit, CORS, Header, Body-Cap, Leak-Hygiene.

Threat-Model (AUDIT §9): Ein-Operator-System, das einen Tunnel-/Reverse-Proxy-
Exposure-Schritt überleben soll. Bewusst KEINE IP-basierte localhost-Ausnahme —
Tunnel forwarden von 127.0.0.1, das würde jeden Client durchwinken (XFF-Falle).
Auth ist rein tokenbasiert:

  - kein api_token gesetzt → offen (Preflight erzwingt localhost-Bind)
  - api_token gesetzt → gültiges Bearer/Query-Token auf geschützten Endpoints

Rate-Limiter ist in-process (thread-sicher), unabhängig vom Redis-Bus.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict, deque
from threading import Lock

from collect.config import settings

# Modulebene nötig: mit `from __future__ import annotations` löst FastAPI die
# Dependency-Typ-Hints über die Modul-Globals auf — ein nur lokal importiertes
# Request würde als Query-Parameter fehlinterpretiert (→ 422).
try:
    from fastapi import Request
except ImportError:  # fastapi ist optionales Extra [api]
    Request = None

logger = logging.getLogger("collect.api")


# ── Auth ─────────────────────────────────────────────────────────────────

def token_configured() -> bool:
    return bool(settings.api_token)


def token_valid(presented: str | None) -> bool:
    """Konstant-zeit-Vergleich gegen Timing-Leaks. Leeres/None → ungültig."""
    if not presented or not settings.api_token:
        return False
    return hmac.compare_digest(presented, settings.api_token)


def extract_token(headers, query_params) -> str | None:
    """Bearer-Header (REST) oder ?token= (WebSocket — Browser können keine
    WS-Handshake-Header setzen)."""
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    tok = query_params.get("token")
    return tok.strip() if tok else None


class RateLimiter:
    """Sliding-Window pro Key (Token bzw. Peer-IP). Thread-sicher — POST läuft
    im uvicorn-Threadpool, WS im Event-Loop."""

    def __init__(self, per_minute: int):
        self.limit = per_minute
        self.window = 60.0
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """→ (erlaubt, retry_after_s). retry_after nur bei Ablehnung > 0."""
        now = time.monotonic()
        with self._lock:
            dq = self._hits[key]
            while dq and now - dq[0] >= self.window:
                dq.popleft()
            if len(dq) >= self.limit:
                retry = max(1, int(self.window - (now - dq[0])) + 1)
                return False, retry
            dq.append(now)
            return True, 0


# Prozessweite Limiter (ein API-Prozess)
_expensive = RateLimiter(settings.api_rate_expensive)
_light = RateLimiter(settings.api_rate_light)


def _client_key(request_or_ws) -> str:
    """Rate-Limit-Key: Token wenn vorhanden, sonst Peer-IP. Bewusst NICHT
    X-Forwarded-For (fälschbar) — hinter Proxy schützt der Token."""
    client = getattr(request_or_ws, "client", None)
    ip = client.host if client else "unknown"
    tok = extract_token(request_or_ws.headers, request_or_ws.query_params)
    return f"tok:{tok[:16]}" if tok else f"ip:{ip}"


def check_rate(request, tier: str = "expensive") -> None:
    """Wirft HTTP 429 bei Überschreitung. tier ∈ {expensive, light}."""
    from fastapi import HTTPException
    limiter = _expensive if tier == "expensive" else _light
    ok, retry = limiter.check(_client_key(request))
    if not ok:
        raise HTTPException(status_code=429, detail="Rate limit exceeded",
                            headers={"Retry-After": str(retry)})


# ── FastAPI-Dependency + Preflight + Middleware ──────────────────────────

def require_auth(tier: str = "expensive"):
    """Dependency-Factory: prüft Rate-Limit, dann Token (falls konfiguriert)."""
    from fastapi import HTTPException

    def dependency(request: Request) -> None:
        check_rate(request, tier)
        if not token_configured():
            return  # offener Modus (Preflight garantiert localhost-Bind)
        tok = extract_token(request.headers, request.query_params)
        if not token_valid(tok):
            logger.warning("Auth-Fehlschlag: %s %s von %s", request.method,
                           request.url.path,
                           request.client.host if request.client else "?")
            raise HTTPException(status_code=401, detail="Unauthorized")

    return dependency


def ws_authorized(ws) -> bool:
    """WebSocket-Auth (kein Dependency-Pfad): Rate-Limit + Token."""
    ok, _ = _expensive.check(_client_key(ws))
    if not ok:
        return False
    if not token_configured():
        return True
    return token_valid(extract_token(ws.headers, ws.query_params))


def exposure_preflight() -> None:
    """Vor dem Bind: Nicht-localhost ohne Token → Start verweigern."""
    if not settings.api_is_localhost and not token_configured():
        raise RuntimeError(
            f"API-Bind auf {settings.api_host} (nicht localhost) OHNE "
            "COLLECT_API_TOKEN — verweigert. Setze ein Token oder binde an "
            "127.0.0.1. (TLS terminiert der Reverse-Proxy, nicht die App.)")


def add_security(app) -> None:
    """CORS-Allowlist, Security-Header, Body-Cap, generischer Fehler-Handler."""
    from fastapi import Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    origins = ([o.strip() for o in settings.api_cors_origins.split(",") if o.strip()]
               or settings.api_default_origins)
    app.add_middleware(
        CORSMiddleware, allow_origins=origins,
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        # Body-Cap (Content-Length; streamende Riesen-Bodies fängt uvicorn)
        clen = request.headers.get("content-length")
        if clen and clen.isdigit() and int(clen) > settings.api_max_body_bytes:
            return JSONResponse({"detail": "Payload too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(Exception)
    async def _generic(request: Request, exc: Exception):
        # Interna nur ins Log, generische Antwort an den Client (Leak-Hygiene)
        logger.exception("Unbehandelter Fehler bei %s", request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
