"""Web-Recherche — SearXNG-Adapter für Vault-FALLBACK + expliziten Trigger.

SearXNG ist selbst-gehostet (kein Cloud-Zwang). Liefert JSON-Suchergebnisse
mit title, url, content/Snippet. Der Client ist transportfrei und testbar.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from collect.config import settings

logger = logging.getLogger(__name__)

_WEB_TRIGGER = re.compile(
    settings.web_search_trigger, re.IGNORECASE)


def is_web_request(query: str) -> bool:
    """Expliziter Trigger: 'recherchiere im web', 'such im internet' etc."""
    return bool(_WEB_TRIGGER.search(query))


def clean_query_for_search(query: str) -> str:
    """Trigger-Phrasen entfernen, saubere Such-Query übrig lassen."""
    return _WEB_TRIGGER.sub("", query).strip().strip(".,;:!?").strip()


class WebSearcher:
    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None):
        self.base_url = (base_url or settings.web_search_url).rstrip("/")
        self.timeout = timeout or settings.web_search_timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "collect2/0.1"

    def search(self, query: str, max_results: Optional[int] = None) -> list[dict]:
        max_results = max_results or settings.web_search_results
        if not query.strip():
            return []
        try:
            resp = self._session.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "language": "en",
                        "safesearch": "1", "categories": "general"},
                timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            results = []
            for r in data.get("results", [])[:max_results]:
                results.append({
                    "title": str(r.get("title", "")),
                    "url": str(r.get("url", "")),
                    "content": str(r.get("content", "") or r.get("snippet", "")),
                })
            return results
        except requests.ConnectionError:
            logger.warning("SearXNG nicht erreichbar (%s)", self.base_url)
            return []
        except Exception as e:
            logger.warning("Web-Suche fehlgeschlagen: %s", e)
            return []

    def fetch_page(self, url: str, max_chars: int = 3000) -> str:
        try:
            resp = self._session.get(url, timeout=self.timeout,
                                     headers={"Accept": "text/html,text/plain"})
            resp.raise_for_status()
            text = resp.text[:max_chars * 2]
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            return text[:max_chars]
        except Exception:
            return ""
