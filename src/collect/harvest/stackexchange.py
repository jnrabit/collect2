"""StackExchange Source — Q&A-Wissen via Stack Exchange API.

Physics, Math, Stats, AI, Philosophy — Praxis-Wissen in Q&A-Form.
Gratis, kein API-Key nötig (Rate-Limit 30/min mit Key, niedriger ohne).
Liefert Doc-Dicts (id, title, content, source) für den Vault-Ingest.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)

SE_API = "https://api.stackexchange.com/2.3"

SITES = [
    "physics",
    "math",
    "stats",
    "cstheory",
]

SE_TAGS = [
    "entropy", "thermodynamics", "information-theory",
    "quantum-mechanics", "statistical-mechanics", "complex-systems",
    "nonlinear-dynamics", "chaos", "phase-transition",
]


def search_stackexchange(tag: str, site: str = "physics",
                         limit: int = 30) -> list[dict]:
    """Sucht StackExchange nach Tag, gibt Doc-Dicts zurück (id=prefix SE-)."""
    params = {
        "site": site,
        "tagged": tag,
        "pagesize": min(limit, 100),
        "order": "desc",
        "sort": "votes",
        "filter": "withbody",
    }
    url = f"{SE_API}/questions?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = r.read().decode("utf-8")
            data = json.loads(raw)
    except Exception as e:
        msg = str(e)
        if "502" in msg or "503" in msg:
            time.sleep(10)
            return []
        if "backoff" in msg.lower():
            time.sleep(15)
            return []
        logger.debug("StackExchange error (%s): %s", site, msg[:80])
        return []

    docs = []
    for q in data.get("items", []):
        title = (q.get("title") or "").strip()
        body = (q.get("body_markdown") or q.get("body", ""))
        if not title or not body:
            continue

        # HTML-Reste entfernen
        import re
        body = re.sub(r"<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", body).strip()

        # Top-Antwort dranhängen
        answers = q.get("answers", [])
        if answers:
            best = answers[0]
            abody = best.get("body_markdown") or best.get("body", "")
            abody = re.sub(r"<[^>]+>", " ", abody)
            abody = re.sub(r"\s+", " ", abody).strip()
            body = f"Q: {title}\nA: {abody[:800]}"

        if len(body) < 150:
            continue

        doc_id = f"SE-{int(time.time())}-{hash(title) & 0xFFFFFF:06x}"
        docs.append({
            "id": doc_id,
            "source": f"STACKEXCHANGE_{site.upper()}",
            "sector": "Q&A_KNOWLEDGE",
            "title": f"[{site}] {title}",
            "content": body[:2000],
            "score": q.get("score", 0),
            "tags": q.get("tags", []),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return docs


def harvest_topics(tags: Optional[list[str]] = None,
                   sites: Optional[list[str]] = None,
                   limit_per_tag: int = 10) -> list[dict]:
    tag_list = tags or SE_TAGS
    site_list = sites or SITES
    all_docs = []
    for site in site_list:
        for tag in tag_list:
            docs = search_stackexchange(tag, site, limit=limit_per_tag)
            all_docs.extend(docs)
            if docs:
                logger.info("SE: %s/%s → %d Docs", site, tag, len(docs))
            time.sleep(1.2)  # Rate-Limit ohne Key
    return all_docs
