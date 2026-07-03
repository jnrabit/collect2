"""REST-API — dünnes FastAPI-Gateway vor dem Agenten-Stack.

Nur localhost (settings.api_host), kein Auth-Layer — Härtung (Token,
Capabilities wie vibelikes web/auth.py) kommt vor jedem breiteren Exposure.

    collect-api                      # startet uvicorn
    GET  /api/health                 # Redis + Agenten-Heartbeats
    POST /api/query {"query": "…"}   # synchron, blockiert bis Antwort
    GET  /api/facts                  # verbürgte Fakten
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from collect.config import settings


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    timeout: Optional[float] = Field(default=None, ge=1, le=600)


def create_app():
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="collect2", version="0.1.0")

    @app.get("/api/health")
    def health():
        from collect.status import get_agent_status
        agents = get_agent_status()
        alive = sum(1 for s in agents.values() if s["alive"])
        return {
            "status": "ok" if alive >= 8 else ("degraded" if alive else "down"),
            "agents_alive": alive,
            "agents": agents,
        }

    @app.post("/api/query")
    def query(req: QueryRequest):
        from collect.client import ask
        result = ask(req.query, timeout=req.timeout)
        if result.get("meta", {}).get("timeout"):
            raise HTTPException(status_code=504, detail=result.get("text"))
        return result

    @app.get("/api/facts")
    def facts():
        from pathlib import Path
        db = Path(settings.ossifikat_db)
        if not db.exists():
            return {"facts": []}
        from ossifikat.store import OssifikatStore
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
    uvicorn.run(create_app(), host=settings.api_host, port=settings.api_port,
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
