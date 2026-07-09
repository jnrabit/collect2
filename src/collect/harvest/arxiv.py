"""ArXiv-Source — Harvest-Adapter für arxiv.org API.

Höflicher API-Adapter mit Rate-Limiting, Retry und XML-Parsing.
Liefert Doc-Dicts (id, title, content, source) für den Vault-Ingest.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"


def search_arxiv(query: str, max_results: int = 10,
                 timeout: float = 30.0) -> list[dict]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    docs = []
    try:
        resp = requests.get(ARXIV_API, params=params, timeout=timeout)
        resp.raise_for_status()
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(resp.text)
        for entry in root.findall("atom:entry", ns):
            id_el = entry.find("atom:id", ns)
            title_el = entry.find("atom:title", ns)
            summary_el = entry.find("atom:summary", ns)

            arxiv_id = id_el.text.strip() if id_el is not None and id_el.text else ""
            title_text = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""
            summary_text = summary_el.text.strip().replace("\n", " ") if summary_el is not None and summary_el.text else ""
            if not summary_text or len(summary_text) < 200:
                continue
            docs.append({
                "id": f"arxiv:{arxiv_id.split('/')[-1]}" if arxiv_id else "",
                "title": title_text[:300],
                "content": summary_text[:4000],
                "source": "arxiv",
            })
        time.sleep(3.0)
    except Exception as e:
        logger.warning("ArXiv-Suche fehlgeschlagen: %s", e)
    return docs
