"""Eval-Framework — die Messlatte für Tuning (Profile, Patterns, Prompts, Gewichte).

Zwei Modi, bewusst getrennt:
  Retrieval-Eval (default): deterministisch, OHNE LLM, ~1s/Query — Gate-tauglich.
  --full: zusätzlich Full-Pipeline über den laufenden Service (Latenz,
          Antwort-Länge, Fakten). Rauscht und dauert — NIE als Gate verwenden.

Usage:
  collect-eval                               # Tabelle (Basis-Gewichte)
  collect-eval --profiles precise,balanced   # Side-by-Side-Vergleich
  collect-eval --repeats 3                   # Stabilität (Pflicht für chaos/broad)
  collect-eval --queries eigene.json         # eigenes Query-Set
  collect-eval --baseline write              # lokale Baseline speichern
  collect-eval --ci                          # gegen Baseline; Exit 1 bei Regression
  collect-eval --full                        # Full-Pipeline (Service muss laufen)

Die Baseline liegt in data/ (gitignored) — sie beschreibt DIESEN lokalen
Vault; auf anderer Maschine oder in GitHub-CI wäre sie bedeutungslos.
Workflow: Baseline schreiben → Prompt/Pattern/Gewicht ändern → collect-eval
--ci → grün = committen.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

from collect.config import settings

# Query-Set: query, route ∈ {general, both, code, *}, answerable (None = egal),
# terms = Begriffe, von denen mindestens einer in den Top-3 auftauchen muss.
# Die letzten beiden sind Nonsens-Wächter: sie DÜRFEN NICHT TRUST erreichen.
EVAL_QUERIES: list[dict] = [
    {"query": "What is Apache Spark?", "route": "*", "answerable": True,
     "terms": ["spark"]},
    {"query": "How does the TLS handshake work?", "route": "*", "answerable": True,
     "terms": ["tls", "transport layer", "handshake protocol", "ssl"]},
    {"query": "transformer neural network attention mechanism", "route": "*",
     "answerable": True, "terms": ["attention", "transformer"]},
    {"query": "What is quantum entanglement?", "route": "*", "answerable": True,
     "terms": ["entangle", "quantum"]},
    {"query": "HTTP request methods GET POST", "route": "*", "answerable": True,
     "terms": ["http"]},
    {"query": "How does an HTTP request and response work?", "route": "*",
     "answerable": True, "terms": ["http", "request", "response"]},
    {"query": "What is HTTPS and TLS encryption?", "route": "*", "answerable": True,
     "terms": ["https", "tls", "encrypt", "secur"]},
    {"query": "Was ist der Unterschied zwischen TCP und UDP?", "route": "*",
     "answerable": True, "terms": ["tcp", "udp", "transport"]},
    {"query": "Python global interpreter lock", "route": "*", "answerable": True,
     "terms": ["python", "gil", "interpreter", "thread"]},
    {"query": "xyzzy plugh frobnicate quux blorp", "route": "*",
     "answerable": False, "terms": []},
    {"query": "mein lieblingsessen ist pizza mit ananas", "route": "*",
     "answerable": False, "terms": []},
]

# Zonen-Ordnung fürs Regressions-Urteil: höher = schlechter.
_ZONE_RANK = {"TRUST": 0, "GRAUZONE": 1, "FALLBACK": 2, "—": 3}
_DIST_DRIFT_WARN = 5.0  # Distanz-Drift ab hier als Warnung melden


def load_queries(path: Optional[str] = None) -> list[dict]:
    """Query-Set: Datei-Override (validiert) oder eingebautes Set."""
    if not path:
        return EVAL_QUERIES
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"✗ {path}: erwartet nicht-leere Liste von Query-Objekten")
    for q in data:
        if not isinstance(q, dict) or not q.get("query"):
            raise SystemExit(f"✗ {path}: jedes Objekt braucht 'query'")
        q.setdefault("route", "*")
        q.setdefault("answerable", None)
        q.setdefault("terms", [])
    return data


def build_service():
    """Retrieval-Service in-process — ohne Translator/Decomposer (kein LLM,
    deterministisch), gegen die echten lokalen Vaults."""
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


def run_query(svc, spec: dict, profile: Optional[dict], repeats: int) -> dict:
    """Eine Query, N Wiederholungen → Messzeile."""
    dists, zones, routes, relevant_hits = [], [], [], 0
    top1 = "—"
    for _ in range(repeats):
        r = svc.retrieve(spec["query"], top_k=30, max_hits=5, profile=profile)
        vault = r.general or r.code
        routes.append(r.route)
        zones.append(vault.verdict.zone if vault else "—")
        dists.append(vault.best_distance
                     if vault and vault.best_distance is not None else 999.0)
        tops = vault.hits[:3] if vault else []
        if tops:
            top1 = tops[0].title[:40]
        if spec["terms"]:
            blob = " ".join((h.title + " " + h.content[:300]).lower() for h in tops)
            if any(t in blob for t in spec["terms"]):
                relevant_hits += 1

    answerable = spec["answerable"]
    return {
        "query": spec["query"],
        "route": routes[0] if len(set(routes)) == 1 else f"INSTABIL {sorted(set(routes))}",
        "zones": sorted(set(zones)),
        "zone_worst": max(zones, key=lambda z: _ZONE_RANK.get(z, 3)),
        "zone_stable": len(set(zones)) == 1,
        "dist_mean": round(statistics.mean(dists), 1),
        "dist_range": round(max(dists) - min(dists), 2),
        "relevant_ok": (relevant_hits == repeats) if spec["terms"] else None,
        "answerable_ok": (
            None if answerable is None else
            all(z != "FALLBACK" for z in zones) if answerable
            else all(z != "TRUST" for z in zones)),
        "top1": top1,
    }


def run_eval(svc, queries: list[dict], profile_names: list[str],
             repeats: int) -> dict:
    """→ {profil_name: [zeilen]}. 'basis' = Init-Gewichte (kein Profil-dict)."""
    results = {}
    for name in profile_names:
        profile = None if name == "basis" else settings.get_profile(name)
        results[name] = [run_query(svc, spec, profile, repeats)
                         for spec in queries]
    return results


def summarize(rows: list[dict]) -> dict:
    n_rel = sum(1 for r in rows if r["relevant_ok"])
    n_rel_total = sum(1 for r in rows if r["relevant_ok"] is not None)
    return {
        "zone_stable": f"{sum(1 for r in rows if r['zone_stable'])}/{len(rows)}",
        "relevant": f"{n_rel}/{n_rel_total}",
        "answerable_ok": f"{sum(1 for r in rows if r['answerable_ok'])}/{len(rows)}",
        "max_dist_range": max((r["dist_range"] for r in rows), default=0.0),
    }


def render(results: dict, repeats: int) -> str:
    """Tabelle; bei mehreren Profilen Side-by-Side pro Query."""
    names = list(results)
    out = [f"collect-eval — {len(next(iter(results.values())))} Queries × "
           f"{repeats} Wiederholung(en), Profile: {', '.join(names)}", ""]
    fmt = "  {:10s} {:9s} {:7s} {:>6s} {:>6s}  {:3s} {:3s}  {}"
    for i, row0 in enumerate(results[names[0]]):
        out.append(f"▸ {row0['query'][:70]}")
        out.append(fmt.format("PROFIL", "ZONE", "ROUTE", "DIST", "±", "REL", "ANT", "TOP-1"))
        for name in names:
            r = results[name][i]
            mark = lambda v: "—" if v is None else ("✓" if v else "✗")
            out.append(fmt.format(
                name[:10], r["zone_worst"], str(r["route"])[:7],
                f"{r['dist_mean']:.1f}", f"{r['dist_range']:.1f}",
                mark(r["relevant_ok"]), mark(r["answerable_ok"]), r["top1"]))
        out.append("")
    for name in names:
        s = summarize(results[name])
        out.append(f"Σ {name}: zonen-stabil {s['zone_stable']} · relevant "
                   f"{s['relevant']} · antwortbarkeit {s['answerable_ok']} · "
                   f"max. Distanz-Spannweite {s['max_dist_range']}")
    return "\n".join(out)


# ── Baseline & Regression-Gate ────────────────────────────────────────────

def baseline_path() -> Path:
    return Path(settings.knowledge_vault_file).parent / "eval_baseline.json"


def write_baseline(rows: list[dict], repeats: int) -> Path:
    path = baseline_path()
    path.write_text(json.dumps({
        "written": time.strftime("%Y-%m-%d %H:%M"),
        "repeats": repeats,
        "weights": {"alpha": settings.retrieval_alpha,
                    "beta": settings.retrieval_beta,
                    "gamma": settings.retrieval_gamma,
                    "delta": settings.retrieval_delta},
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def check_regression(rows: list[dict], baseline: dict) -> tuple[list, list]:
    """→ (regressionen, warnungen). Regression: Zone schlechter, Relevanz
    verloren, Antwortbarkeits-Urteil verloren. Warnung: Distanz-Drift."""
    base = {r["query"]: r for r in baseline["rows"]}
    regressions, warnings = [], []
    for r in rows:
        b = base.get(r["query"])
        if b is None:
            warnings.append(f"NEU (nicht in Baseline): {r['query'][:60]}")
            continue
        if _ZONE_RANK.get(r["zone_worst"], 3) > _ZONE_RANK.get(b["zone_worst"], 3):
            regressions.append(
                f"ZONE {b['zone_worst']} → {r['zone_worst']}: {r['query'][:60]}")
        if b.get("relevant_ok") and not r.get("relevant_ok"):
            regressions.append(f"RELEVANZ verloren: {r['query'][:60]}")
        if b.get("answerable_ok") and not r.get("answerable_ok"):
            regressions.append(f"ANTWORTBARKEIT verloren: {r['query'][:60]}")
        drift = r["dist_mean"] - b["dist_mean"]
        if drift > _DIST_DRIFT_WARN:
            warnings.append(
                f"Distanz +{drift:.1f} ({b['dist_mean']} → {r['dist_mean']}): "
                f"{r['query'][:60]}")
    return regressions, warnings


# ── Full-Pipeline (manuell, nie als Gate) ─────────────────────────────────

def run_full(queries: list[dict], timeout: float) -> list[dict]:
    """Jede Query durch den LAUFENDEN Service (inkl. LLM) — misst Latenz,
    Antwort-Länge, Zone, Fakten, Tokens. Braucht collect-agents + Ollama."""
    from collect.client import ask
    rows = []
    for spec in queries:
        t0 = time.time()
        result = ask(spec["query"], timeout=timeout, show_progress=False)
        meta = result.get("meta", {})
        rows.append({
            "query": spec["query"][:50],
            "zone": meta.get("zone", "?"),
            "chars": len(result.get("text", "")),
            "secs": round(time.time() - t0, 1),
            "facts": meta.get("facts_used", 0),
            "tokens": meta.get("tokens"),
            "timeout": bool(meta.get("timeout")),
        })
        print(f"  {rows[-1]['zone']:9s} {rows[-1]['secs']:6.1f}s "
              f"{rows[-1]['chars']:6d} Zeichen  {spec['query'][:50]}",
              file=sys.stderr)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profiles", default="basis",
                    help="Kommagetrennt: basis,precise,balanced,broad,resonant,"
                         "chaos,adaptive (default: basis)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--queries", help="eigenes Query-Set (JSON-Datei)")
    ap.add_argument("--baseline", choices=["write"],
                    help="write: aktuelles Basis-Ergebnis als Baseline speichern")
    ap.add_argument("--ci", action="store_true",
                    help="gegen Baseline diffen; Exit 1 bei Regression")
    ap.add_argument("--full", action="store_true",
                    help="Full-Pipeline über den laufenden Service (LLM, langsam)")
    ap.add_argument("--json", action="store_true", help="maschinenlesbar")
    ap.add_argument("--timeout", type=float, default=None,
                    help="Timeout pro Query im --full-Modus")
    args = ap.parse_args()

    queries = load_queries(args.queries)

    if args.full:
        print(f"Full-Pipeline: {len(queries)} Queries über den Service "
              f"(LLM — das dauert) …", file=sys.stderr)
        rows = run_full(queries, args.timeout or settings.query_timeout)
        print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json else
              "\n".join(f"{r['zone']:9s} {r['secs']:6.1f}s {r['chars']:6d} Z "
                        f"{'⏰' if r['timeout'] else '  '} {r['query']}"
                        for r in rows))
        return 0

    profile_names = [p.strip() for p in args.profiles.split(",") if p.strip()]
    known = set(settings.retrieval_profiles) | {"basis"}
    unknown = [p for p in profile_names if p not in known]
    if unknown:
        raise SystemExit(f"✗ Unbekannte Profile: {unknown} — bekannt: {sorted(known)}")
    sampling = [p for p in profile_names
                if p != "basis" and settings.get_profile(p).get("sampling")]
    if sampling and args.repeats < 3:
        print(f"⚠️  {sampling} nutzen Sampling (nicht deterministisch) — "
              f"--repeats 3+ empfohlen.", file=sys.stderr)

    print("Lade Vaults …", file=sys.stderr)
    svc = build_service()
    results = run_eval(svc, queries, profile_names, args.repeats)

    if args.baseline == "write":
        path = write_baseline(results.get("basis") or next(iter(results.values())),
                              args.repeats)
        print(f"✓ Baseline geschrieben: {path}")
        return 0

    if args.ci:
        path = baseline_path()
        if not path.is_file():
            raise SystemExit(f"✗ Keine Baseline ({path}) — erst "
                             f"'collect-eval --baseline write'.")
        baseline = json.loads(path.read_text(encoding="utf-8"))
        rows = results.get("basis") or next(iter(results.values()))
        regressions, warnings = check_regression(rows, baseline)
        for w in warnings:
            print(f"⚠️  {w}")
        for r in regressions:
            print(f"✗ {r}")
        if regressions:
            print(f"\n✗ {len(regressions)} Regression(en) gegen Baseline "
                  f"vom {baseline.get('written')}.")
            return 1
        print(f"✓ Keine Regression ({len(rows)} Queries, Baseline "
              f"{baseline.get('written')}, {len(warnings)} Warnung(en)).")
        return 0

    print(json.dumps(results, ensure_ascii=False, indent=2) if args.json
          else render(results, args.repeats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
