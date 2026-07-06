"""Wikipedia-Quell-Adapter — MediaWiki-API, höflich (maxlag, Backoff).

Portiert aus vibelike/harvest.py (_wiki_fetch + Seed-Iteration), gehärtet:
maxlag=5 (Server drosselt selbst), 429-Retry mit Retry-After-Respekt,
Rate-Limit mit Jitter. Seeds kommen aus der Such-API zum Topic plus
optionaler Link-Expansion Tiefe 1 — thematisch fokussiert statt Random-Walk.
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API = {"en": "https://en.wikipedia.org/w/api.php",
       "de": "https://de.wikipedia.org/w/api.php"}
USER_AGENT = ("collect2-harvester/1.0 "
              "(https://github.com/jnrabit/collect2; jakobnotter89@googlemail.com)")

SLEEP_BASE = 0.6
SLEEP_JITTER = 0.4
RETRY_MAX = 4
RETRY_BACKOFF = (5, 15, 45, 90)
MIN_EXTRACT_LEN = 200   # Stubs überspringen


def _get(api_url: str, params: dict) -> dict:
    """GET mit 429-Retry (Retry-After bevorzugt, sonst Backoff-Tabelle)."""
    url = api_url + "?" + urllib.parse.urlencode(params)
    for attempt in range(RETRY_MAX):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < RETRY_MAX - 1:
                ra = e.headers.get("Retry-After", "")
                wait = (int(ra) if ra.isdigit() else RETRY_BACKOFF[attempt]) + random.uniform(0, 2)
                logger.warning("429 — backoff %.1fs (%d/%d)", wait, attempt + 1, RETRY_MAX)
                time.sleep(wait)
                continue
            raise
    return {}


class WikipediaSource:
    name = "wikipedia"

    def __init__(self, lang: str = "en"):
        if lang not in API:
            raise ValueError(f"Sprache {lang!r} nicht unterstützt ({list(API)})")
        self.lang = lang
        self.api = API[lang]

    def search_titles(self, topic: str, limit: int) -> list[str]:
        """Such-API → Titel, relevanteste zuerst."""
        data = _get(self.api, {
            "action": "query", "format": "json", "list": "search",
            "srsearch": topic, "srlimit": min(limit, 500), "srnamespace": "0",
        })
        hits = data.get("query", {}).get("search", [])
        return [h["title"] for h in hits]

    def links_of(self, title: str, limit: int) -> list[str]:
        """Interne Links einer Seite (für Tiefe-1-Expansion)."""
        data = _get(self.api, {
            "action": "query", "format": "json", "prop": "links",
            "titles": title, "plnamespace": "0", "pllimit": min(limit, 500),
        })
        pages = data.get("query", {}).get("pages", {})
        out = []
        for page in pages.values():
            out += [ln["title"] for ln in page.get("links", [])]
        return out

    def fetch(self, title: str) -> dict | None:
        """Extract + Metadaten eines Titels → Doc-Dict (oder None bei Stub/fehlt)."""
        data = _get(self.api, {
            "action": "query", "format": "json", "titles": title,
            "prop": "extracts|info", "exintro": "0", "explaintext": "1",
            "inprop": "url", "maxlag": "5",
        })
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return None
        page = next(iter(pages.values()))
        extract = page.get("extract", "")
        if "missing" in page or len(extract) < MIN_EXTRACT_LEN:
            return None
        real_title = page.get("title", title)
        return {
            "id": f"WIKI-{self.lang}-{real_title.replace(' ', '_')}",
            "source": "WIKIPEDIA",
            "sector": "GENERAL_KNOWLEDGE",
            "title": real_title,
            "content": extract,
            "url": page.get("fullurl", ""),
            "lang": self.lang,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def harvest(self, topic: str, limit: int, expand: bool = True,
                known_ids: set | None = None, on_progress=None):
        """Generator über frische Doc-Dicts zum Topic (max. `limit`).

        known_ids: bereits im Vault vorhandene IDs → werden gar nicht erst
        gefetcht (idempotenter Re-Harvest). expand: Tiefe-1-Link-Expansion,
        falls die Such-Titel nicht reichen.
        """
        known = known_ids or set()
        seeds = self.search_titles(topic, limit)
        # Expansion nur wenn nötig — spart API-Calls
        if expand and len(seeds) < limit and seeds:
            for seed in list(seeds[:3]):
                seeds += self.links_of(seed, limit)
                if len(seeds) >= limit * 2:
                    break

        seen_titles, yielded = set(), 0
        for title in seeds:
            if yielded >= limit:
                break
            key = title.lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            # ID-Vorabprüfung ohne Netzwerk
            probe_id = f"WIKI-{self.lang}-{title.replace(' ', '_')}"
            if probe_id in known:
                continue
            try:
                doc = self.fetch(title)
            except Exception as e:
                logger.warning("fetch %r fehlgeschlagen: %s", title[:40], e)
                doc = None
            time.sleep(SLEEP_BASE + random.uniform(0, SLEEP_JITTER))
            if not doc or doc["id"] in known:
                continue
            yielded += 1
            if on_progress:
                on_progress(yielded, limit, doc["title"])
            yield doc
