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
        """Pro Nachricht {query}: Progress-Events streamen, dann die Antwort.
        client.stream() blockiert (Redis-Pubsub) → läuft im Thread-Pool.
        Gesprächskontext lebt pro Verbindung (Seite neu laden = neues Gespräch)."""
        from collect.client import stream

        if not ws_authorized(ws):
            await ws.close(code=1008)  # Policy Violation (Auth/Rate)
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
                events = stream(query, history=list(history))
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
        result = ask(req.query, timeout=req.timeout)
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
