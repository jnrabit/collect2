"""API-Härtung: Auth-Erzwingung, Rate-Limit, CORS, Header, Body-Cap, Preflight."""

import pytest

from collect.config import settings

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from collect import api, api_security  # noqa: E402


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ossifikat_db", tmp_path / "o.db")
    # Rate-Limiter pro Test frisch (prozessweite Singletons zurücksetzen)
    monkeypatch.setattr(api_security, "_expensive",
                        api_security.RateLimiter(settings.api_rate_expensive))
    monkeypatch.setattr(api_security, "_light",
                        api_security.RateLimiter(settings.api_rate_light))

    def build(token=""):
        monkeypatch.setattr(settings, "api_token", token)
        # /api/query soll nicht wirklich den Stack rufen
        import collect.client
        monkeypatch.setattr(collect.client, "ask",
                            lambda q, timeout=None: {"text": "ok", "meta": {}})
        return TestClient(api.create_app())

    return build


# ── Konstant-zeit-Vergleich ──────────────────────────────────────────────

def test_token_valid_constant_time(monkeypatch):
    monkeypatch.setattr(settings, "api_token", "geheim123")
    assert api_security.token_valid("geheim123") is True
    assert api_security.token_valid("falsch") is False
    assert api_security.token_valid("") is False
    assert api_security.token_valid(None) is False


def test_extract_token_bearer_and_query():
    class H(dict):
        pass
    assert api_security.extract_token({"authorization": "Bearer abc"}, {}) == "abc"
    assert api_security.extract_token({}, {"token": "xyz"}) == "xyz"
    assert api_security.extract_token({}, {}) is None


# ── Offener Modus (kein Token) ───────────────────────────────────────────

def test_open_mode_allows_everything(app_client):
    c = app_client(token="")
    assert c.post("/api/query", json={"query": "hi"}).status_code == 200
    assert c.get("/api/facts").status_code == 200
    assert c.get("/api/health").status_code == 200


# ── Token-Modus ──────────────────────────────────────────────────────────

def test_query_requires_token(app_client):
    c = app_client(token="s3cr3t")
    assert c.post("/api/query", json={"query": "hi"}).status_code == 401
    assert c.post("/api/query", json={"query": "hi"},
                  headers={"Authorization": "Bearer falsch"}).status_code == 401
    ok = c.post("/api/query", json={"query": "hi"},
                headers={"Authorization": "Bearer s3cr3t"})
    assert ok.status_code == 200


def test_facts_requires_token(app_client):
    c = app_client(token="s3cr3t")
    assert c.get("/api/facts").status_code == 401
    assert c.get("/api/facts",
                 headers={"Authorization": "Bearer s3cr3t"}).status_code == 200


def test_health_and_chat_stay_public_with_token(app_client):
    c = app_client(token="s3cr3t")
    assert c.get("/api/health").status_code == 200
    assert c.get("/chat").status_code == 200


def test_health_omits_per_agent_detail(app_client, monkeypatch):
    c = app_client(token="")
    import collect.status
    monkeypatch.setattr(collect.status, "get_agent_status",
                        lambda *a, **k: {"llm": {"alive": True, "age_s": 1}})
    body = c.get("/api/health").json()
    assert "agents" not in body           # kein Per-Agent-Leak
    assert set(body) == {"status", "agents_alive"}


# ── Rate-Limiting ────────────────────────────────────────────────────────

def test_rate_limit_returns_429_with_retry_after(app_client, monkeypatch):
    monkeypatch.setattr(settings, "api_rate_light", 3)
    monkeypatch.setattr(api_security, "_light", api_security.RateLimiter(3))
    c = app_client(token="")
    for _ in range(3):
        assert c.get("/api/facts").status_code == 200
    r = c.get("/api/facts")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


def test_rate_limit_tiers_are_independent():
    exp = api_security.RateLimiter(1)
    assert exp.check("k")[0] is True
    assert exp.check("k")[0] is False       # 2. Aufruf blockt
    assert exp.check("anderer")[0] is True  # anderer Key eigenes Budget


# ── CORS ─────────────────────────────────────────────────────────────────

def test_cors_allows_localhost_rejects_foreign(app_client):
    c = app_client(token="")
    origin = f"http://127.0.0.1:{settings.api_port}"
    ok = c.get("/api/health", headers={"Origin": origin})
    assert ok.headers.get("access-control-allow-origin") == origin
    eve = c.get("/api/health", headers={"Origin": "http://evil.example.com"})
    assert eve.headers.get("access-control-allow-origin") != "http://evil.example.com"


# ── Security-Header + Body-Cap ───────────────────────────────────────────

def test_security_headers_present(app_client):
    c = app_client(token="")
    h = c.get("/api/health").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "no-referrer"


def test_body_cap_rejects_large_payload(app_client, monkeypatch):
    monkeypatch.setattr(settings, "api_max_body_bytes", 100)
    c = app_client(token="")
    r = c.post("/api/query", json={"query": "x" * 500})
    assert r.status_code == 413


# ── WebSocket-Auth ───────────────────────────────────────────────────────

def test_ws_requires_token_when_configured(app_client, monkeypatch):
    import collect.client

    def fake_stream(q, history=None):        # Generator → hat .close()
        yield ("answer", {"text": "ok", "meta": {}})

    monkeypatch.setattr(collect.client, "stream", fake_stream)
    c = app_client(token="wssecret")
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/chat") as ws:  # kein Token
            ws.receive_json()
    # Mit Token verbindet + antwortet
    with c.websocket_connect("/ws/chat?token=wssecret") as ws:
        ws.send_json({"query": "hi"})
        assert ws.receive_json()["type"] == "answer"


# ── Preflight ────────────────────────────────────────────────────────────

def test_preflight_blocks_exposed_without_token(monkeypatch):
    monkeypatch.setattr(settings, "api_host", "0.0.0.0")
    monkeypatch.setattr(settings, "api_token", "")
    with pytest.raises(RuntimeError, match="verweigert"):
        api_security.exposure_preflight()


def test_preflight_allows_exposed_with_token(monkeypatch):
    monkeypatch.setattr(settings, "api_host", "0.0.0.0")
    monkeypatch.setattr(settings, "api_token", "tok")
    api_security.exposure_preflight()  # kein Fehler


def test_preflight_allows_localhost_without_token(monkeypatch):
    monkeypatch.setattr(settings, "api_host", "127.0.0.1")
    monkeypatch.setattr(settings, "api_token", "")
    api_security.exposure_preflight()  # kein Fehler
