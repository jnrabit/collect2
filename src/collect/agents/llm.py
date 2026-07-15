"""LLMAgent — geerdete Antwort-Synthese (Ollama, lokal).

Zustandsmaschine statt Blocking-Wait: llm_request registriert eine pendende
Anfrage mit `needs` (welche Retrieval-Beiträge kommen); die *_response-Events
füllen sie auf. Sind alle Beiträge da, wird generiert. Liegen ALLE Zonen im
Hard-Fallback, wird das Generieren übersprungen (spart 10–30s Ollama-Zeit;
der ResponseAgent unterdrückt die Antwort ohnehin).
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from collect.agents.base import BaseAgent
from collect.agents import ollama
from collect.bus import Message
from collect.config import settings
from collect.retrieval.zones import ZONE_FALLBACK, ZONE_GRAY, fallback_suppressed

SYSTEM_PROMPT = (
    "Du bist ein Wissensassistent. Beantworte die Frage des Nutzers auf Deutsch, "
    "GESTÜTZT auf die bereitgestellten Quellen. Antworte AUSFÜHRLICH und gut "
    "strukturiert: erkläre Zusammenhänge, gib relevante Details und Beispiele aus "
    "den Quellen wieder statt nur Stichworte. Nenne die verwendeten Quellen — bei "
    "Web-Quellen mit dem Link (URL). Wenn die Quellen die Frage nicht abdecken, "
    "sage das ehrlich. Erfinde keine Fakten."
)


class LLMAgent(BaseAgent):
    name = "llm"

    def __init__(self, bus, generate_fn: Optional[Callable] = None, grounder=None):
        """grounder: optionaler FactGrounder — verbürgte Ossifikat-Fakten
        werden dem Prompt autoritativ vorangestellt.
        generate_fn: (prompt, system=, on_token=) → str ODER (str, stats) —
        Rückgabe-Formate beider ollama-Varianten werden akzeptiert."""
        super().__init__(bus)
        self.generate = generate_fn or ollama.generate_streaming
        self.grounder = grounder
        self._pending: dict[str, dict] = {}  # cid → {query, needs, contribs}
        # Beiträge, die VOR dem llm_request eintreffen (Race: Retrieval kann
        # schneller sein als die Zustellung des Requests) — begrenzt gepuffert,
        # mit Zeitstempel: verwaiste cids (Request kam nie) altern raus.
        self._early: dict[str, dict] = {}
        self._early_ts: dict[str, float] = {}

    def subscriptions(self):
        return {
            "llm_request": self.on_request,
            "retrieval_response": self.on_contribution("retrieval"),
            "code_retrieval_response": self.on_contribution("code_retrieval"),
            "file_response": self.on_contribution("file"),
            "web_response": self.on_contribution("web"),
        }

    _EARLY_TTL = 120.0  # Sek.: verwaiste Early-Beiträge (Request kam nie) verwerfen
    _EARLY_MAX = 256

    def on_request(self, msg: Message) -> None:
        cid = msg.correlation_id
        self._early_ts.pop(cid, None)
        self._pending[cid] = {
            "query": msg.data.get("original_query") or msg.data.get("query", ""),
            "effective": msg.data.get("query", ""),
            "needs": set(msg.data.get("needs") or ["retrieval"]),
            "contribs": self._early.pop(cid, {}),
            "history": msg.data.get("history") or [],
            "referential": bool(msg.data.get("referential")),
            "rewritten_query": msg.data.get("rewritten_query"),
        }
        self._maybe_generate(cid)

    def _evict_early(self) -> None:
        """Abgelaufene (TTL) und überzählige (FIFO) Early-Einträge verwerfen."""
        now = time.monotonic()
        for cid in [c for c, ts in self._early_ts.items() if now - ts > self._EARLY_TTL]:
            self._early.pop(cid, None)
            self._early_ts.pop(cid, None)
        while len(self._early) > self._EARLY_MAX:
            oldest = next(iter(self._early))
            self._early.pop(oldest, None)
            self._early_ts.pop(oldest, None)

    def on_contribution(self, kind: str):
        def handler(msg: Message) -> None:
            cid = msg.correlation_id
            state = self._pending.get(cid)
            if state is None:
                # Request (noch) nicht da — puffern statt verlieren
                self._early.setdefault(cid, {})[kind] = msg.data
                self._early_ts.setdefault(cid, time.monotonic())
                self._evict_early()
                return
            state["contribs"][kind] = msg.data
            self._maybe_generate(cid)
        return handler

    def _should_auto_web(self, state: dict) -> bool:
        """Auto-Web nur wenn der Vault UNSICHER ist (alle Beiträge GRAUZONE/
        FALLBACK, kein TRUST-Treffer) — misst 'Vault nicht souverän', nicht
        bloß 'gar nichts'. Einmal pro Anfrage."""
        if not (settings.web_search_enabled and settings.web_search_auto):
            return False
        if state.get("web_requested") or "web" in state["contribs"]:
            return False
        # Dateikontext erdet die Antwort bereits → kein Web nötig
        # (Opt-out: COLLECT_WEB_SEARCH_AUTO_WITH_FILE=true)
        if not settings.web_search_auto_with_file:
            file_c = state["contribs"].get("file")
            if file_c and file_c.get("chunks"):
                return False
        zones = [c.get("zone") for k, c in state["contribs"].items()
                 if k in ("retrieval", "code_retrieval")]
        return bool(zones) and all(z in (ZONE_GRAY, ZONE_FALLBACK) for z in zones)

    def _maybe_generate(self, cid: str) -> None:
        state = self._pending.get(cid)
        if state is None or not state["needs"] <= set(state["contribs"]):
            return

        # Auto-Web: bei unsicherem Vault Web NACHFORDERN und DARAUF warten,
        # damit es in einem Zug mitsynthetisiert wird (wie der explizite Pfad).
        # Der LLM kennt die Retrieval-Zone hier bereits — anders als der frühere
        # ResponseAgent-Trigger, der erst NACH dem (übersprungenen) LLM-Lauf
        # feuerte und die Web-Treffer nie synthetisierte.
        if self._should_auto_web(state):
            state["web_requested"] = True
            state["needs"].add("web")
            self.progress(cid, "web_auto", "Vault unsicher — Web-Recherche…")
            # Bei Rückbezug die umgeschriebene (kontext-aufgelöste) Query suchen,
            # nicht das bloße "und was gibt es dazu?".
            web_query = state.get("rewritten_query") or state["query"]
            self.publish("web_request", "web_request",
                         {"query": web_query, "explicit": False}, cid)
            return  # wartet auf web_response → _maybe_generate erneut

        del self._pending[cid]

        # Verbürgte Fakten (Ossifikat) — autoritatives Grounding
        facts = []
        if self.grounder:
            try:
                facts = self.grounder.relevant_facts(state["effective"] or state["query"])
            except Exception as e:
                self.log.warning("%s: Fakt-Grounding fehlgeschlagen: %s", cid[:8], e)

        # Dateiinhalt/Web-Recherche erdet die Antwort → nie überspringen.
        # Sonst: alle Vault-Zonen FALLBACK und keine Fakten → Halluzination.
        # Entscheidung zentral in zones.fallback_suppressed (Flag-abschaltbar).
        file_contrib = state["contribs"].get("file")
        has_file = bool(file_contrib and file_contrib.get("chunks"))
        web_contrib = state["contribs"].get("web")
        has_web = bool(web_contrib and web_contrib.get("count"))
        zones = [c.get("zone") for k, c in state["contribs"].items()
                 if k in ("retrieval", "code_retrieval")]
        all_fallback = bool(zones) and all(z == ZONE_FALLBACK for z in zones)
        if fallback_suppressed(all_fallback,
                               grounded=bool(facts or has_file or has_web)):
            self.log.info("%s: alle Zonen FALLBACK, keine Fakten/Datei/Web — LLM übersprungen", cid[:8])
            self.publish("llm_response", "llm_response",
                         {"content": "", "skipped": True, "model": "", "facts_used": 0}, cid)
            return

        self.progress(cid, "llm_generating", "Antwort wird generiert…")
        prompt = self._build_prompt(state, facts)
        try:
            content, stats = self._generate_streamed(cid, prompt)
            self.publish("llm_response", "llm_response", {
                "content": content.strip(), "skipped": False,
                "model": settings.main_model,
                "facts_used": len(facts),
                **stats,  # eval_count, tok_per_s (falls Streaming-Backend)
            }, cid)
            self.log.info("%s: Antwort generiert (%d Zeichen, %d Fakten, %s tok/s)",
                          cid[:8], len(content), len(facts), stats.get("tok_per_s"))
        except Exception as e:
            self.log.error("%s: LLM-Fehler: %s", cid[:8], e)
            self.publish("llm_response", "llm_response",
                         {"content": "", "skipped": False, "error": str(e),
                          "facts_used": len(facts)}, cid)

    def _generate_streamed(self, cid: str, prompt: str) -> tuple[str, dict]:
        # Interim-Batching: pro Ollama-Chunk (≈1 Token) publizieren würde den
        # Bus fluten; gesammelt wird bis flush_chars Zeichen oder flush_secs.
        flush_chars = settings.llm_flush_chars
        flush_secs = settings.llm_flush_secs
        buf: list[str] = []
        tokens = [0]
        last_flush = [time.monotonic()]

        def flush():
            if buf:
                self.publish("llm_interim", "llm_interim",
                             {"delta": "".join(buf), "tokens": tokens[0]}, cid)
                buf.clear()
                last_flush[0] = time.monotonic()

        def on_token(delta: str):
            buf.append(delta)
            tokens[0] += 1
            if (sum(len(x) for x in buf) >= flush_chars
                    or time.monotonic() - last_flush[0] >= flush_secs):
                flush()

        try:
            result = self.generate(prompt, system=SYSTEM_PROMPT, on_token=on_token)
        except TypeError:
            # Backend ohne on_token-Support (z.B. non-streaming generate)
            result = self.generate(prompt, system=SYSTEM_PROMPT)
        flush()
        if isinstance(result, tuple):
            return result[0], (result[1] or {})
        return result, {}

    def _build_prompt(self, state: dict, facts: list | None = None) -> str:
        # Reihenfolge der Retrieval-Agenten BEIBEHALTEN (lexikalisch rerankt —
        # ein erneutes Distanz-Sortieren würde das Rerank zunichte machen);
        # bei both-Route General/Code abwechselnd verschränken.
        per_vault = []
        for kind in ("retrieval", "code_retrieval"):
            contrib = state["contribs"].get(kind)
            if not contrib or contrib.get("zone") == ZONE_FALLBACK:
                continue  # entfernte Treffer erden, Fallback-Treffer nicht
            per_vault.append(contrib.get("hits", []))
        docs = []
        for i in range(max((len(v) for v in per_vault), default=0)):
            for vault_hits in per_vault:
                if i < len(vault_hits):
                    docs.append(vault_hits[i])

        # Verbürgte Fakten VOR den Quellen — bei Widerspruch haben sie Vorrang
        fact_block = ""
        if facts:
            fl = "\n".join(f"- {f['content']}" for f in facts)
            fact_block = ("VERBÜRGTE FAKTEN (vom Nutzer bestätigt — als gesichert "
                          "behandeln, bei Widerspruch haben sie Vorrang vor den "
                          f"QUELLEN):\n{fl}\n\n")

        # Direkt gelesener Dateiinhalt — per Definition geerdet, autoritativ
        file_block = ""
        file_contrib = state["contribs"].get("file")
        if file_contrib and file_contrib.get("chunks"):
            blocks = []
            for c in file_contrib["chunks"]:
                blocks.append(f"### {c['path']}\n{c['text']}")
            file_block = ("DATEIINHALT (vom Nutzer adressiert — direkt gelesen, "
                          "als gesichert behandeln; beantworte die Frage GESTÜTZT "
                          "auf diesen Inhalt):\n" + "\n\n".join(blocks) + "\n\n")

        # Web-Recherche — mit Links (URLs). Bei EXPLIZITER Anfrage ist Web die
        # primäre Quelle (der Nutzer wollte eine Web-Suche); bei Auto-Web
        # ergänzend. Frühere Rahmung ('nicht verifiziert') wertete Web zu stark
        # ab → der LLM ignorierte die Treffer und verankerte auf (irrelevanten)
        # Vault-Docs.
        web_block = ""
        web_explicit = False
        web_contrib = state["contribs"].get("web")
        if web_contrib and web_contrib.get("hits"):
            web_explicit = bool(web_contrib.get("explicit"))
            wblocks = []
            for i, h in enumerate(web_contrib["hits"][:settings.llm_web_max], 1):
                url = h.get("source", "")
                wblocks.append(f"[W{i}] {h.get('title', '?')} — {url}\n"
                               f"{h.get('content', '')[:settings.llm_web_chars]}")
            if web_explicit:
                web_block = ("WEB-RECHERCHE (der Nutzer hat explizit eine Web-Suche "
                             "angefordert — beantworte die Frage GESTÜTZT auf diese "
                             "aktuellen Web-Quellen und zitiere die verwendeten "
                             "Links):\n" + "\n\n".join(wblocks) + "\n\n")
            else:
                web_block = ("WEB-RECHERCHE (aktuell aus dem Internet, ergänzend zu "
                             "den Vault-Quellen — nutze sie und zitiere die Links):\n"
                             + "\n\n".join(wblocks) + "\n\n")

        parts = []
        for i, doc in enumerate(docs[:settings.llm_top_docs], 1):
            title = doc.get("title") or doc.get("doc_id", f"Quelle {i}")
            parts.append(f"[{i}] {title}:\n{doc.get('content', '')[:settings.llm_doc_chars]}")

        # Gesprächskontext relevanz-gesteuert: bei einem Rückbezug ("und wofür?")
        # voll nutzen; bei einem Themenwechsel (eigenständige Frage) nur als
        # Hintergrund mit klarer Ignorier-Anweisung — sonst blutet das alte
        # Thema in eine unverwandte Antwort (beobachtet: Quanten-Kontext in
        # einer Verdachtsfalle-Frage).
        history_block = ""
        history = state.get("history") or []
        if history:
            lines = []
            for turn in history[-3:]:
                lines.append(f"Nutzer: {str(turn.get('q', ''))[:300]}")
                lines.append(f"Du: {str(turn.get('a', ''))[:500]}")
            convo = "\n".join(lines)
            if state.get("referential"):
                history_block = ("BISHERIGES GESPRÄCH (die aktuelle FRAGE bezieht "
                                 "sich darauf — nutze den Kontext):\n" + convo + "\n\n")
            else:
                history_block = ("BISHERIGES GESPRÄCH (nur Hintergrund): Falls die "
                                 "FRAGE ein ANDERES Thema ist, IGNORIERE dieses "
                                 "Gespräch vollständig und stelle KEINE erzwungene "
                                 "Verbindung her.\n" + convo + "\n\n")

        context = "\n\n".join(parts) if parts else "(keine Quellen verfügbar)"

        # Truncation-Guard: alles außer den Vault-Quellen (Frage, Instruktion,
        # Fakten, Datei, Web, History) hat Vorrang; nur die Vault-Quellen werden
        # gekürzt, falls das Budget überschritten wird. num_ctx=16384 macht das
        # im Normalfall unnötig — reines Sicherheitsnetz gegen Overflow.
        head = f"{history_block}{fact_block}{file_block}"
        tail = (f"\n\nFRAGE: {state['query']}\n\n"
                f"Antworte ausführlich und gestützt auf die obigen Quellen; "
                f"nenne die verwendeten Web-Links.")
        fixed_len = len(head) + len(web_block) + len(tail) + 40
        vault_budget = max(0, settings.llm_prompt_char_budget - fixed_len)
        if len(context) > vault_budget:
            context = context[:vault_budget] + "\n…(gekürzt)"
        vault_block = f"QUELLEN (lokaler Vault):\n{context}\n\n"
        # Bei expliziter Web-Anfrage: Web VOR den Vault-Quellen (primär);
        # sonst Vault zuerst, Web ergänzend danach.
        body = f"{web_block}{vault_block}" if web_explicit else f"{vault_block}{web_block}"
        return f"{head}{body}{tail.lstrip()}"
