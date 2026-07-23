"""collect-harvest explore — autonomer Knowledge-Graph-Crawler.

Kombiniert SEC_WIKI_CRAWLER's Link-Following mit TF-IDF-Query-Generierung:
  1. SURF: Wikipedia-Links folgen (wie SEC_WIKI_CRAWLER)
  2. QUERY: Aus gesammelten Seiten TF-IDF-Terme extrahieren
  3. DEEPEN: ArXiv mit den extrahierten Termen durchsuchen
  4. Wiederhole ab 1 mit neuen Links aus Wikipedia + ArXiv

Kein manuelles Topic — der Crawler entdeckt selbstständig.

USAGE:
  collect-harvest explore  --lang de
  collect-harvest explore  --cycles 50 --limit 200
  collect-harvest explore  --dry-run
"""

from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import threading
import time
from pathlib import Path

from collect.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("collect-explore")

# ── Seed-Themen (Fallback wenn Link-Kette erschöpft) ──────────────────────────

SEED_TOPICS_DE = [
    "Leben", "Bewusstsein", "Realität", "Universum", "Zeit",
    "Mensch", "Künstliche Intelligenz", "Sprache", "Kultur",
    "Mathematik", "Physik", "Biologie", "Chemie", "Geschichte",
    "Technologie", "Kunst", "Musik", "Politik", "Wirtschaft",
    "Psychologie", "Soziologie", "Philosophie", "Ethik", "Religion",
    "Chaosforschung", "Informationstheorie", "Thermodynamik",
    "Quantenphysik", "Nichtlineare Dynamik", "Emergenz",
]

SEED_TOPICS_EN = [
    "Life", "Consciousness", "Reality", "Universe", "Time",
    "Artificial intelligence", "Language", "Culture",
    "Mathematics", "Physics", "Biology", "Chemistry", "History",
    "Technology", "Art", "Music", "Politics", "Economics",
    "Psychology", "Sociology", "Philosophy", "Ethics", "Religion",
    "Chaos theory", "Information theory", "Thermodynamics",
    "Quantum mechanics", "Nonlinear dynamics", "Emergence",
    "Self-organization", "Complex systems", "Entropy",
]

# ── Wikipedia Link-Crawler ────────────────────────────────────────────────────


class WikiSurfer:
    """Folgt Wikipedia-Links und entdeckt neue Seiten autonom.

    Algorithmus aus SEC_WIKI_CRAWLER:
      SURF: Links der letzten Seite folgen → erste unbekannte wählen
      SEED: Wenn Kette erschöpft → Zufall aus Seed-Liste
      Auch bei bekannten Seiten: Links extrahieren für nächsten Surf
    """

    def __init__(self, lang: str = "de"):
        self.lang = lang
        self._chain_links: list[str] = []
        self._wikipedia = None

    @property
    def wikipedia(self):
        if self._wikipedia is None:
            import wikipedia
            wikipedia.set_lang(self.lang)
            self._wikipedia = wikipedia
        return self._wikipedia

    def _get_page(self, topic: str):
        try:
            return self.wikipedia.page(topic, auto_suggest=False)
        except Exception as e:
            cls = type(e).__name__
            if "Disambiguation" in cls:
                try:
                    opts = e.options[:5] if hasattr(e, "options") else []
                    if opts:
                        choice = random.choice(opts)
                        logger.debug("Disambiguation: %s → %s", topic, choice)
                        return self.wikipedia.page(choice, auto_suggest=False)
                except Exception:
                    pass
            return None

    def next_topic(self, known_titles: set[str]) -> tuple[str, str]:
        """→ (topic, mode) — mode is SURF or SEED"""
        # SURF: ersten unbekannten Link aus der Kette wählen
        if self._chain_links:
            random.shuffle(self._chain_links)
            for link in self._chain_links:
                if link not in known_titles:
                    return link, "SURF"

        # SEED: Kette erschöpft → Zufall aus Seed-Liste
        seeds = SEED_TOPICS_DE if self.lang == "de" else SEED_TOPICS_EN
        topic = random.choice(seeds)
        self._chain_links = []
        return topic, "SEED"

    def harvest_one(self, known_titles: set[str], known_ids: set[str],
                    _abort: threading.Event) -> dict | None:
        if _abort.is_set():
            return None

        topic, mode = self.next_topic(known_titles)
        logger.info("WIKI-%s: '%s'", mode, topic)

        page = self._get_page(topic)
        if page is None:
            logger.debug("  Dead end: %s", topic)
            return None

        # Links für nächste Runde extrahieren — auch bei bekannten Seiten
        try:
            self._chain_links = list(page.links)
        except Exception:
            pass

        if page.title in known_titles:
            logger.debug("  '%s' bereits bekannt — Links übernommen", page.title)
            return None

        try:
            summary = str(page.summary or "")
        except Exception as e:
            logger.debug("  '%s' summary-Fehler (%s) — übersprungen", page.title, type(e).__name__)
            return None
        if len(summary) < 200:
            logger.debug("  '%s' zu kurz (%d chars)", page.title, len(summary))
            return None

        doc_id = f"WIKI-{int(time.time())}-{random.randint(1000, 9999)}"
        if doc_id in known_ids:
            return None

        logger.info("  📄 %s (%d chars)", page.title, len(summary))
        return {
            "id": doc_id,
            "source": "WIKI_CRAWLER",
            "sector": "GENERAL_KNOWLEDGE",
            "title": page.title,
            "content": summary,
            "url": str(page.url) if page.url else "",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


# ── ArXiv-Deepener ────────────────────────────────────────────────────────────


def _extract_query_terms(text: str, n: int = 3) -> str:
    """TF-IDF-artige Term-Extraktion (vereinfacht, ohne globalen IDF-Cache)."""
    import re
    from collections import Counter

    stop = {
        "using", "based", "model", "method", "results", "paper", "study",
        "analysis", "approach", "propose", "proposed", "present", "show",
        "that", "this", "with", "from", "have", "been", "were", "their",
        "which", "about", "also", "than", "other", "through", "between",
        "different", "while", "where", "there", "these", "being",
    }
    words = re.findall(r"\b[a-zA-Z]{5,}\b", text.lower())
    scored: Counter = Counter()
    for w in words:
        if w not in stop:
            scored[w] += 1
    top = [w for w, _ in scored.most_common(8)]
    chosen = random.sample(top, min(n, len(top)))
    return " ".join(chosen)


def _arxiv_single(topic: str, limit: int, known_ids: set[str],
                  _abort: threading.Event) -> list[dict]:
    """Eine ArXiv-Suche, gibt frische Docs zurück."""
    if _abort.is_set():
        return []
    try:
        from collect.harvest.arxiv import search_arxiv
        docs = search_arxiv(topic, limit)
        fresh = [d for d in docs if d["id"] not in known_ids]
        for d in fresh:
            known_ids.add(d["id"])
        return fresh
    except Exception as e:
        logger.warning("ArXiv-Fehler '%s': %s", topic, e)
        return []


# ── Explore-Loop ──────────────────────────────────────────────────────────────


def run_explore(lang: str = "de", cycles: int = 0, limit: int = 200,
                dry_run: bool = False, arxiv_queries: int = 5) -> int:
    """Haupt-Loop: Wikipedia-Crawl → Query-Extraktion → ArXiv-Deepening.

    cycles=0 → unbegrenzt. limit=0 → unbegrenzt.
    """
    from collect.harvest.ingest import VaultIngest
    from collect.retrieval.vault import Vault

    vault = Vault(settings.knowledge_vault_file)
    known_ids = {str(d.get("id")) for d in vault.load()}
    known_titles = {str(d.get("title", "")).strip() for d in vault.load()}
    logger.info("Vault: %d Docs, %d Titel bekannt", len(known_ids), len(known_titles))

    surfer = WikiSurfer(lang)
    ingest = VaultIngest()
    abort = threading.Event()
    all_docs: list[dict] = []
    total_wiki = 0
    total_arxiv = 0

    def _handle_sig(sig, frame):
        print("\n⏸️  Abbruch — speichere gesammelte Docs...")
        abort.set()

    old_int = signal.signal(signal.SIGINT, _handle_sig)
    old_term = signal.signal(signal.SIGTERM, _handle_sig)

    try:
        cycle = 0
        while not abort.is_set():
            if cycles > 0 and cycle >= cycles:
                break
            if limit > 0 and len(all_docs) >= limit:
                break

            cycle += 1
            logger.info("── Explore-Zyklus %d/%s ──", cycle, str(cycles) if cycles > 0 else "∞")

            # 1. Wikipedia SURF
            doc = surfer.harvest_one(known_titles, known_ids, abort)
            if doc:
                known_titles.add(doc["title"])
                known_ids.add(doc["id"])
                all_docs.append(doc)
                total_wiki += 1

                # 2. Query aus der neuen Seite extrahieren
                query = _extract_query_terms(
                    doc["title"] + " " + doc["content"], n=3
                )
                if query and arxiv_queries > 0:
                    logger.info("  🔍 ArXiv: '%s'", query)
                    arxiv_docs = _arxiv_single(query, max(5, arxiv_queries),
                                               known_ids, abort)
                    for ad in arxiv_docs:
                        known_titles.add(str(ad.get("title", "")))
                        all_docs.append(ad)
                        total_arxiv += 1
                    time.sleep(random.uniform(1.0, 2.0))  # Höflichkeit
            else:
                time.sleep(1.0)

            # Persistieren alle 10 Docs
            if len(all_docs) >= 10 and len(all_docs) % 10 < 2:
                logger.info("  💾 Zwischenstand: %d Docs (W:%d A:%d)",
                            len(all_docs), total_wiki, total_arxiv)
                if not dry_run and all_docs:
                    result = ingest.ingest(all_docs[-10:], dry_run=dry_run)
                    if result.committed:
                        logger.info("  ✅ %d Docs in Vault geschrieben", result.committed)
                    all_docs = all_docs[-10:] if result.committed else all_docs

            time.sleep(random.uniform(2.0, 4.0))  # Wikipedia-Rate-Limit

    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)

    # Rest speichern
    if all_docs and not dry_run:
        result = ingest.ingest(all_docs, dry_run=dry_run)
        logger.info("Abschluss-Ingest: %s", result.summary())

    logger.info("Explore beendet: %d Wiki + %d ArXiv = %d Docs",
                total_wiki, total_arxiv, total_wiki + total_arxiv)
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(
        description="collect-harvest explore — autonomer Knowledge-Graph-Crawler",
        epilog="Wikipedia SURF + TF-IDF → ArXiv DEEPEN. Kein manuelles Topic.",
    )
    ap.add_argument("--lang", default="de", choices=["en", "de"])
    ap.add_argument("--cycles", type=int, default=0,
                    help="Anzahl Crawl-Zyklen (0=unbegrenzt)")
    ap.add_argument("--limit", type=int, default=0,
                    help="max. neue Docs insgesamt (0=unbegrenzt)")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur sammeln, nicht in Vault schreiben")
    ap.add_argument("--arxiv-queries", type=int, default=5,
                    help="ArXiv-Docs pro extrahierter Query")
    args = ap.parse_args()

    return run_explore(
        lang=args.lang,
        cycles=args.cycles,
        limit=args.limit,
        dry_run=args.dry_run,
        arxiv_queries=args.arxiv_queries,
    )


if __name__ == "__main__":
    sys.exit(main())
