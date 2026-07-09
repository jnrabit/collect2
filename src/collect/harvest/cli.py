"""collect-harvest — manueller Vault-Ingest von Quellen.

    collect-harvest wikipedia "HTTP protocol" --limit 300 --lang en
    collect-harvest arxiv "transformer attention" --limit 10
    collect-harvest rfc --limit 5

Idempotent: bereits vorhandene Docs (per ID) werden nicht erneut gefetcht.
Der laufende Agenten-Stack sieht neue Docs erst nach Neustart (RAM-Kopie).
Bewusst KEIN Daemon/Scheduler — Vault-Writes bleiben ein bewusster Auslöser.
"""

from __future__ import annotations

import argparse
import sys

from collect.config import settings


def _known_ids() -> set:
    from collect.retrieval.vault import Vault
    try:
        return {str(d.get("id")) for d in Vault(settings.knowledge_vault_file).load()}
    except Exception:
        return set()


def main() -> int:
    ap = argparse.ArgumentParser(description="Vault-Ingest von einer Quelle")
    ap.add_argument("source", choices=["wikipedia", "arxiv", "rfc"])
    ap.add_argument("topic", nargs="?", default="", help="Thema/Suchbegriff")
    ap.add_argument("--limit", type=int, default=200, help="max. neue Docs")
    ap.add_argument("--lang", default="en", choices=["en", "de"])
    ap.add_argument("--dry-run", action="store_true",
                    help="nur sammeln + prüfen, NICHT in den Vault schreiben")
    ap.add_argument("--no-expand", action="store_true",
                    help="keine Link-Expansion (Wikipedia)")
    args = ap.parse_args()

    from collect.harvest.ingest import VaultIngest

    if args.source == "rfc":
        from collect.harvest.rfc import harvest_rfcs
        print(f"Harvest: RFC (neueste {args.limit})")
        known = _known_ids()
        docs = harvest_rfcs(args.limit)
        docs = [d for d in docs if d["id"] not in known]
        print(f"{len(docs)} frische RFCs gesammelt. Ingest…")
    elif args.source == "arxiv":
        from collect.harvest.arxiv import search_arxiv
        print(f"Harvest: arxiv '{args.topic}' (limit={args.limit})")
        known = _known_ids()
        docs = search_arxiv(args.topic, args.limit)
        docs = [d for d in docs if d["id"] not in known]
        print(f"{len(docs)} frische ArXiv-Papers gesammelt. Ingest…")
    else:
        if not args.topic:
            print("Fehler: Topic erforderlich für Wikipedia.")
            return 1
        from collect.harvest.wikipedia import WikipediaSource
        print(f"Harvest: wikipedia '{args.topic}' (lang={args.lang}, "
              f"limit={args.limit}{', DRY-RUN' if args.dry_run else ''})")
        known = _known_ids()
        print(f"Vault kennt {len(known):,} Docs.")
        source = WikipediaSource(lang=args.lang)

        def fetched(n, total, title):
            print(f"  [{n}/{total}] {title[:60]}")

        docs = list(source.harvest(args.topic, args.limit,
                                   expand=not args.no_expand,
                                   known_ids=known, on_progress=fetched))
        print(f"\n{len(docs)} frische Docs gesammelt. Ingest…")

    ingest = VaultIngest()
    result = ingest.ingest(docs, dry_run=args.dry_run)
    print("\n" + result.summary())
    if result.error:
        return 1
    if result.committed:
        print("→ Stack neu starten, damit die Docs in den RAM geladen werden: "
              "systemctl --user restart collect-agents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
