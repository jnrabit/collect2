"""collect-harvest — manueller Vault-Ingest von Quellen.

    collect-harvest wikipedia "HTTP protocol" --limit 300 --lang en
    collect-harvest arxiv "transformer attention" --limit 10
    collect-harvest rfc --limit 5
    collect-harvest all "quantum chaos"           # Wikipedia + ArXiv + RFC
    collect-harvest semantic                       # Frontier-Paper via Semantic Scholar
    collect-harvest openalex                       # Foundation-Paper via OpenAlex
    collect-harvest gutenberg                      # Klassische Texte via Project Gutenberg
    collect-harvest stackexchange                  # Q&A-Wissen via StackExchange
    collect-harvest deep                           # alle Quellen (autonomer Themen-Katalog)
    collect-harvest explore                        # Wikipedia-SURF + ArXiv-DEEPEN (autonom)

Idempotent: bereits vorhandene Docs (per ID) werden nicht erneut gefetcht.
Der laufende Agenten-Stack sieht neue Docs erst nach Neustart (RAM-Kopie).
Bewusst KEIN Daemon/Scheduler — Vault-Writes bleiben ein bewusster Auslöser.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from collect.config import settings


def _known_ids() -> set:
    from collect.retrieval.vault import Vault
    try:
        return {str(d.get("id")) for d in Vault(settings.knowledge_vault_file).load()}
    except Exception:
        return set()


def _harvest_all(topic: str, lang: str, limit: int, dry_run: bool,
                 known: set, _abort: threading.Event) -> tuple[list[dict], int]:
    """Thread-Worker für eine Quelle. Gibt (docs, skipped_by_abort) zurück."""
    from collect.harvest.ingest import VaultIngest
    from collect.harvest.wikipedia import WikipediaSource
    from collect.harvest.arxiv import search_arxiv
    from collect.harvest.rfc import harvest_rfcs

    docs: list[dict] = []
    skipped = 0

    if _abort.is_set():
        return docs, skipped

    # Wikipedia
    try:
        print(f"\n── Wikipedia '{topic}' (lang={lang}) ──")
        source = WikipediaSource(lang=lang)
        for doc in source.harvest(topic, limit, expand=True, known_ids=known):
            if _abort.is_set():
                skipped = 1
                break
            if doc["id"] not in known:
                docs.append(doc)
                known.add(doc["id"])
                print(f"  wiki: {doc.get('title', '?')[:70]}")
    except Exception as e:
        print(f"  ⚠️ Wikipedia-Fehler: {e}")
        if _abort.is_set():
            skipped = 1

    if _abort.is_set():
        return docs, skipped

    # ArXiv
    try:
        print(f"\n── ArXiv '{topic}' ──")
        arxiv_docs = search_arxiv(topic, limit)
        for doc in arxiv_docs:
            if _abort.is_set():
                skipped = 1
                break
            if doc["id"] not in known and len(docs) < limit:
                docs.append(doc)
                known.add(doc["id"])
                print(f"  arxiv: {doc.get('title', '?')[:70]}")
    except Exception as e:
        print(f"  ⚠️ ArXiv-Fehler: {e}")

    if _abort.is_set():
        return docs, skipped

    # RFC
    try:
        print(f"\n── RFC (neueste {min(limit, 50)}) ──")
        rfc_docs = harvest_rfcs(min(limit, 50))
        for doc in rfc_docs:
            if _abort.is_set():
                skipped = 1
                break
            if doc["id"] not in known and len(docs) < limit:
                docs.append(doc)
                known.add(doc["id"])
                print(f"  rfc: {doc.get('title', '?')[:70]}")
    except Exception as e:
        print(f"  ⚠️ RFC-Fehler: {e}")

    return docs, skipped


def _collect_all(topic: str, lang: str, limit: int, dry_run: bool,
                 known: set) -> list[dict]:
    """Sequentiell, mit globalem Abbruch via SIGINT/SIGTERM.
    Sammelt von allen drei Quellen bis max. `limit` Docs insgesamt."""
    abort = threading.Event()
    docs: list[dict] = []

    def _handle_sig(sig, frame):
        print("\n⏸️  Abbruch — speichere bereits gesammelte Docs...")
        abort.set()
    old_sigint = signal.signal(signal.SIGINT, _handle_sig)
    old_sigterm = signal.signal(signal.SIGTERM, _handle_sig)

    try:
        d, _ = _harvest_all(topic, lang, limit, dry_run, known, abort)
        docs = d
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)

    return docs


def main() -> int:
    ap = argparse.ArgumentParser(description="Vault-Ingest von einer Quelle")
    ap.add_argument("source", choices=[
        "wikipedia", "arxiv", "rfc", "all", "explore",
        "semantic", "openalex", "gutenberg", "stackexchange", "deep",
    ])
    ap.add_argument("topic", nargs="?", default="", help="Thema/Suchbegriff")
    ap.add_argument("--limit", type=int, default=0,
                    help="max. neue Docs (0 = unbegrenzt)")
    ap.add_argument("--lang", default="en", choices=["en", "de"])
    ap.add_argument("--dry-run", action="store_true",
                    help="nur sammeln + prüfen, NICHT in den Vault schreiben")
    ap.add_argument("--no-expand", action="store_true",
                    help="keine Link-Expansion (Wikipedia)")
    args = ap.parse_args()

    # Effektives Limit: 0 = unbegrenzt für single, 500 für "all"
    if args.limit > 0:
        limit = args.limit
    elif args.source == "all":
        limit = 500
    else:
        limit = 200

    known = _known_ids()
    print(f"Vault: {len(known):,} Docs bekannt.")

    from collect.harvest.ingest import VaultIngest

    if args.source == "explore":
        from collect.harvest.explore import run_explore
        return run_explore(
            lang=args.lang,
            cycles=0,
            limit=limit,
            dry_run=args.dry_run,
            deep=True,
        )
    elif args.source == "semantic":
        from collect.harvest.semantic_scholar import harvest_topics as s2_harvest
        docs = s2_harvest(limit_per_topic=20 if limit == 0 else max(5, limit // 50))
        print(f"{len(docs)} Semantic Scholar Docs gesammelt. Ingest…")
    elif args.source == "openalex":
        from collect.harvest.openalex import harvest_topics as oa_harvest
        docs = oa_harvest(limit_per_topic=30 if limit == 0 else max(3, limit // 20))
        print(f"{len(docs)} OpenAlex Docs gesammelt. Ingest…")
    elif args.source == "gutenberg":
        from collect.harvest.gutenberg import harvest_gutenberg
        docs = harvest_gutenberg(max_books=20 if limit == 0 else max(5, limit // 10))
        print(f"{len(docs)} Gutenberg Docs gesammelt. Ingest…")
    elif args.source == "stackexchange":
        from collect.harvest.stackexchange import harvest_topics as se_harvest
        docs = se_harvest(limit_per_tag=10 if limit == 0 else max(2, limit // 60))
        print(f"{len(docs)} StackExchange Docs gesammelt. Ingest…")
    elif args.source == "deep":
        from collect.harvest.semantic_scholar import harvest_topics as s2_harvest
        from collect.harvest.openalex import harvest_topics as oa_harvest
        from collect.harvest.gutenberg import harvest_gutenberg
        from collect.harvest.stackexchange import harvest_topics as se_harvest

        print(f"\n{'='*60}")
        print(f"🌐 HARVEST DEEP — alle Quellen (max {limit} Docs)")
        print(f"{'='*60}")
        abort = threading.Event()

        def _sig(sig, frame):
            print("\n⏸️  Abbruch — speichere...")
            abort.set()
        old_i = signal.signal(signal.SIGINT, _sig)
        old_t = signal.signal(signal.SIGTERM, _sig)

        try:
            docs: list[dict] = []
            results: dict[str, int] = {}
            for name, fn, kwargs in [
                ("OpenAlex", oa_harvest, {"limit_per_topic": 10, "min_citations": 30}),
                ("Semantic Scholar", s2_harvest, {"limit_per_topic": 15, "year_from": 2020}),
                ("StackExchange", se_harvest, {"limit_per_tag": 8}),
                ("Gutenberg", harvest_gutenberg, {"max_books": 10}),
            ]:
                if abort.is_set():
                    break
                try:
                    new = fn(**kwargs)
                    deduped = [d for d in new if d["id"] not in known]
                    for d in deduped:
                        known.add(d["id"])
                    docs.extend(deduped)
                    results[name] = len(deduped)
                    status = f"{len(deduped)} Docs" if deduped else "keine neuen"
                    print(f"  {name}: {status}")
                except Exception as e:
                    results[name] = -1
                    print(f"  ⚠️ {name}: {e}")
                if abort.is_set():
                    break
                time.sleep(1)
        finally:
            signal.signal(signal.SIGINT, old_i)
            signal.signal(signal.SIGTERM, old_t)

        if limit > 0 and len(docs) > limit:
            docs = docs[:limit]
        print(f"\n{len(docs)} Docs aus allen Quellen gesammelt. Ingest…")
    elif args.source == "all":
        if not args.topic:
            print("Fehler: Topic erforderlich für 'all'.")
            return 1
        print(f"\n{'='*60}")
        print(f"🌐 HARVEST ALL: '{args.topic}' (max {limit} Docs)")
        print(f"{'='*60}")
        docs = _collect_all(args.topic, args.lang, limit, args.dry_run, known)
    elif args.source == "rfc":
        from collect.harvest.rfc import harvest_rfcs
        print(f"Harvest: RFC (neueste {limit})")
        rfc_docs = harvest_rfcs(limit)
        docs = [d for d in rfc_docs if d["id"] not in known]
        print(f"{len(docs)} frische RFCs gesammelt. Ingest…")
    elif args.source == "arxiv":
        from collect.harvest.arxiv import search_arxiv
        print(f"Harvest: arxiv '{args.topic}' (limit={limit})")
        arxiv_docs = search_arxiv(args.topic, limit)
        docs = [d for d in arxiv_docs if d["id"] not in known]
        print(f"{len(docs)} frische ArXiv-Papers gesammelt. Ingest…")
    else:
        if not args.topic:
            print("Fehler: Topic erforderlich für Wikipedia.")
            return 1
        from collect.harvest.wikipedia import WikipediaSource
        print(f"Harvest: wikipedia '{args.topic}' (lang={args.lang}, "
              f"limit={limit}{', DRY-RUN' if args.dry_run else ''})")
        source = WikipediaSource(lang=args.lang)
        def fetched(n, total, title):
            print(f"  [{n}/{total}] {title[:60]}")

        docs = list(source.harvest(args.topic, limit,
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
