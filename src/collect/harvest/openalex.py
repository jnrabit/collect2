"""OpenAlex Source — Foundation-Paper via REST API (250M+ Werke).

Cursor-paginiert, kein API-Key nötig, Qualitätsfilter: >50 Zitationen.
Liefert Doc-Dicts (id, title, content, source) für den Vault-Ingest.

Port aus AI neu/collectors.py:OpenAlexCollector.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)

OA_API = "https://api.openalex.org/works"

FOUNDATION_TOPICS = [
    "nonlinear dynamics", "chaos theory", "dynamical systems",
    "statistical mechanics", "thermodynamics equilibrium",
    "quantum mechanics foundations", "information theory",
    "topology manifolds", "differential equations",
    "bifurcation theory", "ergodic theory",
    "consciousness neuroscience", "cognitive architecture",
    "memory consolidation", "perception attention",
    "neural oscillations", "brain connectivity",
    "complex adaptive systems", "emergence self-organization",
    "network science", "phase transitions critical phenomena",
    "evolutionary dynamics", "systems biology",
]


def _abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    try:
        pos = {}
        for w, ps in inv.items():
            for p in ps:
                pos[p] = w
        return " ".join(pos[i] for i in sorted(pos))
    except Exception:
        return ""


def search_openalex(topic: str, limit: int = 50, min_citations: int = 50) -> list[dict]:
    """Sucht OpenAlex, cursor-paginiert, gibt Doc-Dicts zurück (id=prefix OA-)."""
    params = {
        "search": topic,
        "filter": f"has_abstract:true,type:article,cited_by_count:>{min_citations}",
        "per-page": min(limit, 200),
        "sort": "cited_by_count:desc",
        "select": "title,abstract_inverted_index,publication_year,"
                  "primary_location,cited_by_count",
        "mailto": "collect2@local",
    }
    url = f"{OA_API}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        if "429" in str(e):
            time.sleep(8)
            return []
        logger.debug("OpenAlex error: %s", e)
        return []

    docs = []
    for w in data.get("results", []):
        title = (w.get("title") or "").strip()
        abstract = _abstract(w.get("abstract_inverted_index"))
        if not title or not abstract or len(abstract) < 80:
            continue
        doc_id = f"OA-{int(time.time())}-{hash(title) & 0xFFFFFF:06x}"
        venue = ""
        try:
            venue = (w.get("primary_location") or {}).get(
                "source", {}).get("display_name", "")[:120] or ""
        except Exception:
            pass
        docs.append({
            "id": doc_id,
            "source": "OPENALEX_FOUNDATION",
            "sector": "FOUNDATION",
            "title": title,
            "content": abstract[:2000],
            "year": str(w.get("publication_year") or ""),
            "venue": venue,
            "citations": w.get("cited_by_count", 0),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return docs


def harvest_topics(limit_per_topic: int = 30, topics: Optional[list[str]] = None,
                   min_citations: int = 50) -> list[dict]:
    topic_list = topics or FOUNDATION_TOPICS
    all_docs = []
    for topic in topic_list:
        docs = search_openalex(topic, limit=limit_per_topic, min_citations=min_citations)
        all_docs.extend(docs)
        if docs:
            logger.info("OA: '%s' → %d Docs", topic, len(docs))
        time.sleep(0.5)
    return all_docs
