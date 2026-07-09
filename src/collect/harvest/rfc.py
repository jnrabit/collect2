"""RFC-Source — Harvest-Adapter für rfc-editor.org.

Lädt RFCs als Plain-Text (rfc-editor.org/rfc/rfcNNNN.txt), extrahiert
Titel und Abstract, liefert Doc-Dicts für den Vault-Ingest.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

RFC_BASE = "https://www.rfc-editor.org/rfc"
INDEX_URL = "https://www.rfc-editor.org/rfc-index-latest.txt"


def fetch_rfc_index(limit: int = 50) -> list[str]:
    try:
        resp = requests.get(INDEX_URL, timeout=30)
        resp.raise_for_status()
        rfc_ids = re.findall(r"^(\d{4})", resp.text, re.MULTILINE)
        return sorted(set(rfc_ids), reverse=True)[:limit]
    except Exception as e:
        logger.warning("RFC-Index fehlgeschlagen: %s", e)
        return []


def fetch_rfc(rfc_id: str, timeout: float = 20.0) -> Optional[dict]:
    url = f"{RFC_BASE}/rfc{rfc_id}.txt"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        text = resp.text
        title_match = re.search(r"^\s*(.+?)\s*\n\s*\n", text)
        title = title_match.group(1).strip() if title_match else f"RFC {rfc_id}"

        abstract_start = text.find("Abstract")
        if abstract_start == -1:
            abstract_start = text.find("Status of This Memo")
        abstract = ""
        if abstract_start != -1:
            section = text[abstract_start:abstract_start + 3000]
            lines = section.split("\n")
            for i, line in enumerate(lines[1:], 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("1.") or stripped.startswith("2."):
                    break
                abstract += " " + stripped
            abstract = abstract.strip()[:4000]

        if not abstract or len(abstract) < 200:
            abstract = text[abstract_start:abstract_start + 3000] if abstract_start != -1 else text[:3000]

        return {
            "id": f"rfc:{rfc_id}",
            "title": title[:300],
            "content": abstract[:4000],
            "source": "rfc",
        }
    except Exception as e:
        logger.debug("RFC %s fehlgeschlagen: %s", rfc_id, e)
        return None


def harvest_rfcs(max_rfcs: int = 5) -> list[dict]:
    ids = fetch_rfc_index(max_rfcs)
    docs = []
    for rfc_id in ids:
        doc = fetch_rfc(rfc_id)
        if doc:
            docs.append(doc)
        time.sleep(1.0)
    return docs
