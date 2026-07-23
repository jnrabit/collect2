"""Semantic Scholar Source — Frontier-Paper via REST API.

Kostenlos, kein API-Key nötig (Rate-Limit 1/s ohne Key, 100/s mit).
Liefert Doc-Dicts (id, title, content, source) für den Vault-Ingest.

Port aus AI neu/collectors.py:SemanticScholarCollector.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)

S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,abstract,year,venue,citationCount,isOpenAccess"

FRONTIER_TOPICS = [
    "time crystal", "discrete time crystal", "Floquet time crystal",
    "non-equilibrium thermodynamics", "dissipative structures",
    "driven quantum systems", "Floquet engineering",
    "quantum chaos scarring", "many-body localization",
    "quantum scrambling", "out-of-time-order correlator",
    "eigenstate thermalization hypothesis", "quantum ergodicity breaking",
    "topological phases matter", "anyons non-abelian",
    "strange attractor reconstruction", "Lyapunov spectrum",
    "transient chaos", "chaos synchronization", "hyperchaos",
    "stochastic resonance biology", "quantum biology",
    "quantum cognition", "free energy principle brain",
    "integrated information theory consciousness",
    "renormalization group universality", "self-organized criticality",
    "turbulence cascade energy", "scale-free networks criticality",
]


def search_semantic_scholar(query: str, limit: int = 100, year_from: int = 2020,
                            api_key: str = "") -> list[dict]:
    """Sucht Semantic Scholar, gibt Doc-Dicts zurück (id=prefix S2-)."""
    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": S2_FIELDS,
        "year": f"{year_from}-",
    }
    url = f"{S2_API}?{urllib.parse.urlencode(params)}"
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        if "429" in str(e):
            logger.warning("Semantic Scholar rate-limited, warte 10s")
            time.sleep(10)
            return []
        logger.debug("Semantic Scholar error: %s", e)
        return []

    docs = []
    for p in data.get("data", []):
        title = (p.get("title") or "").strip()
        abstract = (p.get("abstract") or "").strip()
        if not title or not abstract or len(abstract) < 100:
            continue
        doc_id = f"S2-{int(time.time())}-{hash(title) & 0xFFFFFF:06x}"
        docs.append({
            "id": doc_id,
            "source": "SEMANTIC_SCHOLAR",
            "sector": "FRONTIER",
            "title": title,
            "content": abstract[:2000],
            "year": str(p.get("year") or ""),
            "venue": str(p.get("venue") or "")[:120],
            "citations": p.get("citationCount", 0),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return docs


def harvest_topics(limit_per_topic: int = 20, topics: Optional[list[str]] = None,
                   year_from: int = 2020) -> list[dict]:
    """Durchläuft alle Frontier-Themen, sammelt Docs."""
    topic_list = topics or FRONTIER_TOPICS
    all_docs = []
    for topic in topic_list:
        docs = search_semantic_scholar(topic, limit=limit_per_topic, year_from=year_from)
        all_docs.extend(docs)
        if docs:
            logger.info("S2: '%s' → %d Docs", topic, len(docs))
        time.sleep(0.8)  # Rate-Limit ohne API-Key
    return all_docs
