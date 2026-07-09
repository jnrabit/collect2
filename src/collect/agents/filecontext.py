"""FileContextAgent — liest adressierte Dateien und liefert relevante Chunks.

Ephemer: der gelesene Inhalt fließt nur in DIESE Antwort (als geerdeter
Beitrag im Contribution-Manifest), sedimentiert nie in den Vault.
"""

from __future__ import annotations

from collect.agents.base import BaseAgent
from collect.bus import Message
from collect.retrieval.filecontext import build_file_context

MAX_CHUNK_CHARS = 1500  # pro Chunk in der Bus-Nachricht (klein halten)


class FileContextAgent(BaseAgent):
    name = "filecontext"

    def __init__(self, bus, embedder=None):
        super().__init__(bus)
        if embedder is None:
            from collect.retrieval.embedding import get_backend
            embedder = get_backend()
        self.embedder = embedder

    def subscriptions(self):
        return {"file_request": self.on_request}

    def on_request(self, msg: Message) -> None:
        query = msg.data.get("query", "")
        paths = msg.data.get("paths") or []
        cid = msg.correlation_id

        ctx = build_file_context(query, paths, lambda t: self.embedder.embed(t))

        self.publish("file_response", "file_response", {
            "paths": ctx.paths,
            "rejected": ctx.rejected,
            "chunk_count": len(ctx.chunks),
            "chunks": [{"path": c["path"], "text": c["text"][:MAX_CHUNK_CHARS]}
                       for c in ctx.chunks],
        }, cid)
        self.progress(cid, "file_read",
                      f"{len(ctx.paths)} Datei(en), {len(ctx.chunks)} Chunk(s)"
                      + (f", {len(ctx.rejected)} abgelehnt" if ctx.rejected else ""))
        self.log.info("%s: %d Dateien, %d Chunks, %d abgelehnt",
                      cid[:8], len(ctx.paths), len(ctx.chunks), len(ctx.rejected))
