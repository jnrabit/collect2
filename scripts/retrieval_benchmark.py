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


def _build_service():
    from collect.config import settings
    from collect.retrieval.embedding import EmbeddingBackend
    from collect.retrieval.router import CodeRouter
    from collect.retrieval.service import RetrievalService, VaultSearcher

    emb = EmbeddingBackend()
    return RetrievalService(
        embedder=emb, translator=None, decomposer=None,
        router=CodeRouter.from_config(emb.embed_one),
        general=VaultSearcher(settings.knowledge_vault_file,
                              settings.knowledge_cache_file,
                              settings.knowledge_field_file),
        code=VaultSearcher(settings.code_vault_file,
                           settings.code_cache_file,
                           settings.code_field_file),
    )


def run(repeats: int, svc=None) -> dict:
    svc = svc or _build_service()

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


# Follow-up-Szenarien: (Turn-1-Frage, kanonische Kurzantwort, referenzielle
# Folgefrage, erwartete Begriffe in den Top-3). Baseline = Folgefrage roh;
# Vergleich = nach Rewrite (echtes qwen2.5:3b via Ollama).
FOLLOWUP = [
    ("What is Apache Spark?", "Apache Spark ist eine Cluster-Engine für große Datenmengen.",
     "und wofür wird es praktisch eingesetzt?", ["spark"]),
    ("What is quantum entanglement?", "Quantenverschränkung ist eine Korrelation zwischen Teilchen.",
     "wofür kann man das nutzen?", ["entangle", "quantum"]),
    ("How does the TLS handshake work?", "Der TLS-Handshake handelt Schlüssel für sichere Verbindungen aus.",
     "warum ist das sicher?", ["tls", "secur", "encrypt", "ssl", "handshake"]),
    ("Python global interpreter lock", "Der GIL serialisiert Threads im CPython-Interpreter.",
     "wie kann man ihn umgehen?", ["gil", "python", "thread", "interpreter"]),
    ("Was ist der Unterschied zwischen TCP und UDP?", "TCP ist verbindungsorientiert, UDP verbindungslos.",
     "und welches ist schneller?", ["tcp", "udp", "transport"]),
    ("transformer neural network attention mechanism", "Attention gewichtet Eingabe-Teile im Transformer.",
     "wer hat das erfunden?", ["attention", "transformer"]),
]


def _relevant(vault, terms) -> bool:
    tops = vault.hits[:3] if vault else []
    blob = " ".join((h.title + " " + h.content[:300]).lower() for h in tops)
    return any(t in blob for t in terms)


def run_followup(svc) -> dict:
    from collect.retrieval.rewriter import rewrite

    rows = []
    for turn1, answer, followup, terms in FOLLOWUP:
        history = [{"q": turn1, "a": answer}]
        rw = rewrite(followup, history)  # echtes Rewrite-Modell

        base = svc.retrieve(followup, top_k=30, max_hits=5)
        base_vault = base.general or base.code
        base_rel = _relevant(base_vault, terms)

        if rw["applied"]:
            after = svc.retrieve(rw["rewritten"], top_k=30, max_hits=5)
            after_vault = after.general or after.code
            after_rel = _relevant(after_vault, terms)
        else:
            after_vault, after_rel = base_vault, base_rel

        rows.append({
            "followup": followup,
            "rewritten": rw["rewritten"] if rw["applied"] else "(kein Rewrite)",
            "base_rel": base_rel,
            "base_zone": base_vault.verdict.zone if base_vault else "—",
            "after_rel": after_rel,
            "after_zone": after_vault.verdict.zone if after_vault else "—",
        })
    return {
        "rows": rows,
        "summary": {
            "baseline_relevant": sum(r["base_rel"] for r in rows),
            "rewrite_relevant": sum(r["after_rel"] for r in rows),
            "total": len(rows),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-followup", action="store_true",
                    help="Follow-up-Sektion überspringen (braucht Ollama)")
    args = ap.parse_args()

    svc = _build_service()
    result = run(args.repeats, svc=svc)
    if not args.no_followup:
        result["followup"] = run_followup(svc)

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

    if "followup" in result:
        fu = result["followup"]
        print(f"\n{'Folgefrage':<38} {'Rewrite':<44} {'roh':<11} {'rewritten':<11}")
        print("─" * 108)
        for r in fu["rows"]:
            base = f"{'✓' if r['base_rel'] else '✗'} {r['base_zone']}"
            after = f"{'✓' if r['after_rel'] else '✗'} {r['after_zone']}"
            print(f"{r['followup'][:37]:<38} {r['rewritten'][:43]:<44} {base:<11} {after:<11}")
        fs = fu["summary"]
        print("─" * 108)
        print(f"Follow-up relevant: roh {fs['baseline_relevant']}/{fs['total']} → "
              f"mit Rewrite {fs['rewrite_relevant']}/{fs['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
