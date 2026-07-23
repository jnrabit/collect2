"""REST-API + Web-Chat — dünnes FastAPI-Gateway vor dem Agenten-Stack.

Default: localhost-Bind, offen (Zero-Config). Härtung in api_security.py —
mit COLLECT_API_TOKEN wird Auth erzwungen; Nicht-localhost-Bind ohne Token
verweigert der Preflight. TLS terminiert ein Reverse-Proxy, nicht die App.

    collect-api                      # startet uvicorn (mit Exposure-Preflight)
    GET  /chat                       # Web-Chat — öffentlich (Bootstrap-Shell)
    WS   /ws/chat                    # Query rein; Auth via ?token= (teuer)
    GET  /api/health                 # öffentlicher Liveness (Status + Anzahl)
    POST /api/query {"query": "…"}   # Auth (Bearer), synchron (teuer)
    GET  /api/facts                  # Auth (Bearer), verbürgte Fakten (leicht)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from collect.config import settings

# Modulebene nötig: wegen `from __future__ import annotations` löst FastAPI
# die Typ-Hints über die Modul-Globals auf — ein nur in create_app lokal
# importiertes WebSocket würde als Query-Parameter fehlinterpretiert.
try:
    from fastapi import WebSocket
except ImportError:  # fastapi ist optionales Extra [api]
    WebSocket = None

CHAT_HTML = Path(__file__).parent / "web" / "chat.html"


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    timeout: Optional[float] = Field(default=None, ge=1, le=600)
    session_id: Optional[str] = Field(default=None, max_length=64)


def create_app():
    from fastapi import Depends, FastAPI, HTTPException, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, RedirectResponse

    from collect.api_security import add_security, require_auth, ws_authorized

    app = FastAPI(title=settings.display_name, version="0.1.0")
    add_security(app)

    @app.get("/")
    def root():
        """Die nackte URL führt direkt in den Chat."""
        return RedirectResponse(url="/chat")

    @app.get("/chat")
    def chat():
        html = CHAT_HTML.read_text(encoding="utf-8")
        return HTMLResponse(html.replace("{{TITLE}}", settings.display_name))

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        """Pro Nachricht {query, session_id?}: Progress-Events streamen, dann die Antwort.
        client.stream() blockiert (Redis-Pubsub) → läuft im Thread-Pool.
        Gesprächskontext lebt pro Verbindung (Seite neu laden = neues Gespräch)."""
        from collect.client import stream

        if not ws_authorized(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        history: list[dict] = []
        try:
            while True:
                req = await ws.receive_json()
                query = str(req.get("query", "")).strip()
                if not query:
                    await ws.send_json({"type": "error", "detail": "Leere Query."})
                    continue
                session_id = req.get("session_id")
                if session_id and settings.session_enabled:
                    try:
                        from collect.session import SessionStore
                        db_history = SessionStore().get_turns(str(session_id))
                        if db_history:
                            history = list(db_history)
                    except Exception:
                        pass
                events = stream(query, history=list(history),
                                session_id=str(session_id) if session_id else None)
                try:
                    while True:
                        item = await asyncio.to_thread(next, events, None)
                        if item is None:
                            break
                        kind, data = item
                        await ws.send_json({"type": kind, "data": data})
                        if kind == "answer":
                            if not data.get("meta", {}).get("timeout"):
                                history.append({"q": query, "a": data.get("text", "")})
                                del history[:-5]  # letzte 5 Turns reichen
                            break
                finally:
                    events.close()
        except WebSocketDisconnect:
            pass

    @app.get("/api/health")
    def health():
        # Öffentlicher Liveness-Endpoint — nur Status + Anzahl (kein
        # Per-Agent-Detail nach außen, Leak-Hygiene).
        from collect.status import get_agent_status
        alive = sum(1 for s in get_agent_status().values() if s["alive"])
        return {
            "status": "ok" if alive >= 8 else ("degraded" if alive else "down"),
            "agents_alive": alive,
        }

    @app.post("/api/query")
    def query(req: QueryRequest, _auth=Depends(require_auth("expensive"))):
        from collect.client import ask
        result = ask(req.query, timeout=req.timeout, session_id=req.session_id)
        if result.get("meta", {}).get("timeout"):
            raise HTTPException(status_code=504, detail="Query timed out")
        return result

    @app.get("/api/facts")
    def facts(_auth=Depends(require_auth("light"))):
        from pathlib import Path
        db = Path(settings.ossifikat_db)
        if not db.exists():
            return {"facts": []}
        try:
            from ossifikat.store import OssifikatStore
        except ImportError:
            # Submodul nicht initialisiert → leere Liste statt 500
            return {"facts": [], "note": "ossifikat submodule not installed"}
        store = OssifikatStore(str(db))
        try:
            rows = store.query()
        finally:
            store.close()
        return {"facts": [
            {"id": t.id, "subject": t.subject, "predicate": t.predicate,
             "object": t.object, "source": t.source}
            for t in rows
        ]}

    @app.get("/api/vault/doc/{doc_id}")
    def vault_doc(doc_id: str, _auth=Depends(require_auth("light"))):
        """Einzelnes Vault-Dokument (General + Code + Web-Fallback).
        Leicht: nur Vault-Lookup, kein LLM. Frontend ruft auf bei Quellen-Klick."""
        from collect.retrieval.store import VaultStore
        from collect.retrieval.vault import Vault

        stores = [
            ("general", VaultStore(settings.knowledge_vault_file, settings.knowledge_cache_file)),
            ("code", VaultStore(settings.code_vault_file, settings.code_cache_file)),
        ]
        for vault_kind, store in stores:
            doc = store.get_doc(doc_id)
            if doc:
                return {
                    "doc_id": str(doc_id),
                    "vault": vault_kind,
                    "title": str(doc.get("title", "")),
                    "source": str(doc.get("source", "")),
                    "content": str(doc.get("content", doc.get("text", "")))[:3000],
                    "timestamp": str(doc.get("timestamp", "")),
                }
        raise HTTPException(status_code=404, detail=f"Doc {doc_id} nicht gefunden")

    @app.get("/api/sessions")
    def list_sessions(_auth=Depends(require_auth("light"))):
        if not settings.session_enabled:
            return {"sessions": [], "note": "sessions disabled"}
        from collect.session import SessionStore
        return {"sessions": SessionStore().list_sessions()}

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str, _auth=Depends(require_auth("light"))):
        if not settings.session_enabled:
            raise HTTPException(status_code=404, detail="sessions disabled")
        from collect.session import SessionStore
        s = SessionStore().get(sid)
        if not s:
            raise HTTPException(status_code=404, detail="not found")
        return s

    @app.post("/api/sessions")
    def create_session(req: dict, _auth=Depends(require_auth("light"))):
        if not settings.session_enabled:
            raise HTTPException(status_code=404, detail="sessions disabled")
        from collect.session import SessionStore
        import uuid
        sid = req.get("id") or str(uuid.uuid4())[:8]
        return SessionStore().create(sid, req.get("title", ""))

    @app.delete("/api/sessions/{sid}")
    def delete_session(sid: str, _auth=Depends(require_auth("expensive"))):
        if not settings.session_enabled:
            raise HTTPException(status_code=404, detail="sessions disabled")
        from collect.session import SessionStore
        SessionStore().delete(sid)
        return {"deleted": sid}

    return app


def main() -> int:
    import uvicorn

    from collect.api_security import exposure_preflight, token_configured
    exposure_preflight()  # Nicht-localhost ohne Token → RuntimeError vor Bind
    if not settings.api_is_localhost:
        print(f"⚠ API bindet an {settings.api_host} (exponiert) — Token aktiv.")
    elif not token_configured():
        print("API offen auf localhost (kein Token). Für Exposure: "
              "COLLECT_API_TOKEN setzen, siehe .env.example.")
    uvicorn.run(create_app(), host=settings.api_host, port=settings.api_port,
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
