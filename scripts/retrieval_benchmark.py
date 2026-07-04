#!/usr/bin/env python3
"""Retrieval-Benchmark — die Messlatte für jede Retrieval-Änderung.

Festes Query-Set gegen die echten Vaults, N Wiederholungen pro Query:
  - Relevanz: erwartete Begriffe in den Top-3-Treffern?
  - Zonen-Stabilität: gleiche Zone über alle Wiederholungen?
  - Distanz-Varianz: Spannweite der best_distance über Wiederholungen
  - Routing: erwartete Route?

Usage:
  .venv/bin/python scripts/retrieval_benchmark.py            # Tabelle
  .venv/bin/python scripts/retrieval_benchmark.py --json     # maschinenlesbar
  .venv/bin/python scripts/retrieval_benchmark.py --repeats 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys

# (query, erwartete_route ∈ {general, both, code, *}, muss_antwortbar ∈ {True, False, None},
#  erwartete_begriffe in Top-3-Titeln/-Texten)
BENCH = [
    ("What is Apache Spark?", "*", True, ["spark"]),
    ("How does the TLS handshake work?", "*", True, ["tls", "transport layer", "handshake protocol", "ssl"]),
    ("transformer neural network attention mechanism", "*", True, ["attention", "transformer"]),
    ("What is quantum entanglement?", "*", True, ["entangle", "quantum"]),
    ("HTTP request methods GET POST", "*", True, ["http"]),
    ("Was ist der Unterschied zwischen TCP und UDP?", "*", True, ["tcp", "udp", "transport"]),
    ("Python global interpreter lock", "*", True, ["python", "gil", "interpreter", "thread"]),
    ("xyzzy plugh frobnicate quux blorp", "*", False, []),
    ("mein lieblingsessen ist pizza mit ananas", "*", False, []),
]


def run(repeats: int) -> dict:
    from collect.config import settings
    from collect.retrieval.embedding import EmbeddingBackend
    from collect.retrieval.router import CodeRouter
    from collect.retrieval.service import RetrievalService, VaultSearcher

    emb = EmbeddingBackend()
    svc = RetrievalService(
        embedder=emb, translator=None, decomposer=None,
        router=CodeRouter.from_config(emb.embed_one),
        general=VaultSearcher(settings.knowledge_vault_file,
                              settings.knowledge_cache_file,
                              settings.knowledge_field_file),
        code=VaultSearcher(settings.code_vault_file,
                           settings.code_cache_file,
                           settings.code_field_file),
    )

    rows = []
    for query, want_route, answerable, terms in BENCH:
        dists, zones, routes, top_titles, relevant_hits = [], [], [], [], 0
        for _ in range(repeats):
            r = svc.retrieve(query, top_k=30, max_hits=5)
            vault = r.general or r.code
            routes.append(r.route)
            zones.append(vault.verdict.zone if vault else "—")
            dists.append(vault.best_distance if vault and vault.best_distance else 999.0)
            tops = (vault.hits[:3] if vault else [])
            top_titles.append(tops[0].title if tops else "—")
            if terms:
                blob = " ".join((h.title + " " + h.content[:300]).lower() for h in tops)
                if any(t in blob for t in terms):
                    relevant_hits += 1

        rows.append({
            "query": query,
            "route": routes[0] if len(set(routes)) == 1 else f"INSTABIL {set(routes)}",
            "zones": sorted(set(zones)),
            "zone_stable": len(set(zones)) == 1,
            "dist_mean": round(statistics.mean(dists), 1),
            "dist_range": round(max(dists) - min(dists), 2),
            "relevant": f"{relevant_hits}/{repeats}" if terms else "—",
            "relevant_ok": (relevant_hits == repeats) if terms else None,
            "answerable_ok": (
                None if answerable is None else
                all(z != "FALLBACK" for z in zones) if answerable
                else all(z != "TRUST" for z in zones)),
            "top1": top_titles[0][:40],
        })

    n_zone_stable = sum(1 for r in rows if r["zone_stable"])
    n_relevant = sum(1 for r in rows if r["relevant_ok"])
    n_relevant_total = sum(1 for r in rows if r["relevant_ok"] is not None)
    n_answerable = sum(1 for r in rows if r["answerable_ok"])
    max_range = max(r["dist_range"] for r in rows)
    return {
        "repeats": repeats,
        "rows": rows,
        "summary": {
            "zone_stable": f"{n_zone_stable}/{len(rows)}",
            "relevant": f"{n_relevant}/{n_relevant_total}",
            "answerable_ok": f"{n_answerable}/{len(rows)}",
            "max_dist_range": max_range,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = run(args.repeats)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0

    print(f"\n{'Query':<44} {'Route':<9} {'Zonen':<20} {'øDist':>6} {'ΔDist':>6} "
          f"{'Rel.':>5}  Top-1")
    print("─" * 130)
    for r in result["rows"]:
        print(f"{r['query'][:43]:<44} {r['route']:<9} {','.join(r['zones']):<20} "
              f"{r['dist_mean']:>6} {r['dist_range']:>6} {r['relevant']:>5}  {r['top1']}")
    s = result["summary"]
    print("─" * 130)
    print(f"Zonen stabil: {s['zone_stable']} | relevant: {s['relevant']} | "
          f"antwortbar ok: {s['answerable_ok']} | max ΔDist: {s['max_dist_range']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
