"""collect-traces — CLI für die Trace-Pipeline (validate/curate/stats/migrate)."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from collect.config import settings
from collect.traces.collector import get_collector
from collect.traces.schema import TraceEntry, TraceMeta, compute_schema_hash, make_trace_id

# Alt-Site → step_kind der neuen 4er-Taxonomie
_SITE_TO_KIND = {"llm": "answer", "decision": "answer",
                 "codegen": "answer", "repair": "answer", "rewrite": "rewrite"}


def cmd_stats(args) -> int:
    """Bestandszaehlung + Fehler-Inventur (Auftrag Fehler-Inventur).

    Die Fehlerverteilung ist der Blick, der die Finetune-Entscheidung traegt —
    Schwelle in docs/finetune_gate.md.
    """
    from collect.traces.flags import aggregate, load_flags
    collector = get_collector()
    basis = collector.stats()
    agg = aggregate(collector.load_all(), load_flags())
    if args.json:
        print(json.dumps({**basis, "inventur": agg}, ensure_ascii=False, indent=2))
        return 0

    print(f"Traces gesamt:        {agg['total']}   "
          f"(davon gesichtet: {agg['gesichtet']}, "
          f"ungeflaggt/korrekt: {agg['korrekt']})")
    if not agg["gesichtet"]:
        print("\nNoch nichts gesichtet — 'collect-traces review' starten.")
        print(f"step_kinds: {basis['step_kinds']}")
        return 0
    print(f"Fehlerquote:          {agg['fehlerhaft']}/{agg['gesichtet']} = "
          f"{agg['fehlerquote_prozent']} %")
    print("\nFehlerklassen (absteigend):")
    if not agg["klassen"]:
        print("  keine — alle gesichteten Schritte korrekt")
    for k in agg["klassen"]:
        print(f"  {k['klasse']:<18}{k['n']:>3}   {k['anteil_prozent']:>4} %   "
              f"[Quelle: {k['quelle']}]")
    print("\nNach step_kind:")
    for kind, g in sorted(agg["nach_step_kind"].items()):
        print(f"  {kind:<12} gesichtet {g['gesichtet']:>3}/{g['gesamt']:<4} "
              f"fehlerhaft {g['fehlerhaft']}")
    print("\nNach Modell (getrennt lesen — nicht dieselbe Verteilung):")
    for modell, g in sorted(agg["nach_modell"].items()):
        print(f"  {modell:<32} gesichtet {g['gesichtet']:>3}/{g['gesamt']:<4} "
              f"fehlerhaft {g['fehlerhaft']}")
    return 0


def cmd_flag(args) -> int:
    from collect.traces.flags import UnbekannteKlasse, write_flag
    try:
        row = write_flag(args.trace_id, args.klasse, note=args.note or "",
                         flagger=args.flagger)
    except UnbekannteKlasse as e:
        print(f"Fehler: {e}")
        return 2
    except ValueError as e:
        print(f"Fehler: {e}")
        return 2
    print(f"markiert: {row['trace_id']} → {row['klasse']}"
          + (f"  ({row['note']})" if row["note"] else ""))
    return 0


def cmd_review(args) -> int:
    from collect.traces.flags import load_flags
    from collect.traces.review import review_loop
    traces = get_collector().load_all()
    if args.step_kind:
        traces = [t for t in traces
                  if t.get("meta", {}).get("step_kind") == args.step_kind]
    if args.since:
        traces = [t for t in traces
                  if str(t.get("meta", {}).get("timestamp", "")) >= args.since]
    if args.model:
        traces = [t for t in traces
                  if args.model in json.dumps(t.get("meta", {}), ensure_ascii=False)]
    if not traces:
        print("Keine Traces zum Filter.")
        return 1
    z = review_loop(traces, load_flags(), flagger=args.flagger)
    print(f"\nGesichtet: {z['gesichtet']} (korrekt {z['korrekt']}, "
          f"geflaggt {z['geflaggt']}), übersprungen {z['skip']}")
    return 0


def cmd_validate(args) -> int:
    from collect.traces.validate import TraceValidator, print_report
    v = TraceValidator(tokenizer_name=args.tokenizer)
    report = v.validate_all(step_kind=args.step_kind)
    try:
        v.traces_dir.mkdir(parents=True, exist_ok=True)
        (v.traces_dir / "validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2))
    except Exception:  # noqa: BLE001
        pass
    print_report(report)
    return 0 if report["failed"] == 0 else 1


def cmd_curate(args) -> int:
    from collect.traces.curate import build_negatives, curate_interactive
    traces = get_collector().load_all()
    if not traces:
        print("Keine Traces gefunden.")
        return 1
    # Derivate in curated/ — NICHT in traces_dir selbst, sonst liest load_all
    # sie beim naechsten Lauf als Traces wieder ein (non-rekursiver Glob ⇒
    # ein Unterordner ist automatisch ausgeschlossen).
    curated_dir = Path(settings.traces_dir) / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)
    result = curate_interactive(traces, curated_dir / "curation.jsonl",
                                use_prefilter=not args.no_prefilter)
    print(f"\nKuriert: {result['kept']} gut, {result['skipped']} verworfen, "
          f"{result['auto_rejected']} mechanisch vorab abgelehnt")
    if args.negatives:
        negs = build_negatives(traces)
        neg_path = curated_dir / f"{date.today().isoformat()}_synthetic.jsonl"
        with open(neg_path, "a", encoding="utf-8") as f:
            for n in negs:
                f.write(json.dumps(n, ensure_ascii=False) + "\n")
        print(f"Negativbeispiele erzeugt: {len(negs)} → {neg_path}")
    return 0


def cmd_build(args) -> int:
    """Trainings-/Eval-Satz aus Traces + Kurations-Verdikten neu bauen."""
    from collect.traces.curate import build_training_set, load_curated_ids
    traces = get_collector().load_all()
    curated_dir = Path(settings.traces_dir) / "curated"
    curated_ids = load_curated_ids(curated_dir / "curation.jsonl")
    if not curated_ids:
        print(f"Keine Kurations-Verdikte in {curated_dir/'curation.jsonl'} — "
              f"erst 'collect-traces curate' laufen lassen.")
        return 1
    res = build_training_set(traces, curated_ids, eval_n=args.eval_n,
                             negative_ratio=args.negative_ratio)
    curated_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("training_set", res["train"]), ("eval_set", res["eval"])):
        path = curated_dir / f"{name}.jsonl"
        with open(path, "w", encoding="utf-8") as f:  # neu bauen, nicht anhaengen
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{name}: {len(rows)} → {path}")
    print(f"  Train: {res['n_positive']} positiv + {res['n_negative']} negativ")
    return 0


def cmd_migrate(args) -> int:
    """Alt-Traces (flaches data/traces.jsonl) ins neue Schema + rotierende Dir."""
    src = Path(args.source)
    if not src.exists():
        print(f"Quelle nicht gefunden: {src}")
        return 1
    schema_hash = compute_schema_hash()
    dest_dir = Path(settings.traces_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "migrated.jsonl"
    n = skipped = 0
    with open(dest, "w", encoding="utf-8") as out:
        for line in src.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            old = json.loads(line)
            msgs = old.get("messages", [])
            if not msgs:
                skipped += 1
                continue
            om = old.get("meta", {})
            site = om.get("site", "llm")
            extra = {k: v for k, v in om.items()
                     if k not in ("trace_id", "timestamp", "collect2_version",
                                  "tool_schema_hash", "site")}
            entry = TraceEntry(
                messages=msgs, tools=[],
                meta=TraceMeta(
                    trace_id=om.get("trace_id") or make_trace_id(msgs),
                    step_kind=_SITE_TO_KIND.get(site, "answer"),
                    timestamp=om.get("timestamp", ""),
                    collect2_version=om.get("collect2_version", ""),
                    tool_schema_hash=schema_hash,
                    extra={"migrated_from_site": site, **extra}))
            out.write(entry.to_json() + "\n")
            n += 1
    print(f"Migriert: {n} Traces → {dest}  ({skipped} übersprungen)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="collect-traces",
                                 description="Trace-Pipeline für den WorkflowAgent-Finetune.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("stats", help="Zählung + Fehlerverteilung der Inventur")
    ps.add_argument("--json", action="store_true", help="maschinenlesbar")
    ps.set_defaults(fn=cmd_stats)

    from collect.traces.flags import FEHLERKLASSEN
    klassen_hilfe = "\n".join(f"  {k:<18}{v}" for k, v in FEHLERKLASSEN.items())
    pf = sub.add_parser(
        "flag", help="einen Trace mit einer Fehlerklasse markieren",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Fehlerklassen:\n{klassen_hilfe}\n  {'none':<18}"
               f"hebt alle vorherigen Flags dieses Traces auf\n\n"
               f"Rubrik mit Kalibrierbeispielen: docs/traces_fehlerklassen.md")
    pf.add_argument("trace_id")
    pf.add_argument("--class", dest="klasse", required=True,
                    help="Fehlerklasse (siehe unten) oder 'none'")
    pf.add_argument("--note", default="", help="Freitext; Pflicht bei 'sonstiges'")
    pf.add_argument("--flagger", default="", help="wer urteilt (Default $USER)")
    pf.set_defaults(fn=cmd_flag)

    pr = sub.add_parser("review", help="ungesichtete Traces geführt durchgehen")
    pr.add_argument("--since", default=None, help="nur ab Zeitstempel (ISO)")
    pr.add_argument("--step-kind", default=None, help="nur diese Kategorie")
    pr.add_argument("--model", default=None, help="nur Traces mit diesem Modell im meta")
    pr.add_argument("--flagger", default="",
                    help="wer urteilt (Default $USER) — Mensch und Assistent "
                         "muessen spaeter trennbar sein")
    pr.set_defaults(fn=cmd_review)

    pv = sub.add_parser("validate", help="Traces durchs echte Template prüfen")
    pv.add_argument("--tokenizer", default=None)
    pv.add_argument("--step-kind", default=None,
                    help="nur diese Kategorie prüfen (z. B. rewrite)")
    pv.set_defaults(fn=cmd_validate)

    pc = sub.add_parser("curate", help="Interaktive Kuration + Negativ-Synthese")
    pc.add_argument("--negatives", action="store_true", help="auch Negativbeispiele erzeugen")
    pc.add_argument("--no-prefilter", action="store_true",
                    help="mechanischen Vor-Filter aus (alle Traces manuell sichten)")
    pc.set_defaults(fn=cmd_curate)

    pb = sub.add_parser("build", help="Trainings-/Eval-Satz reproduzierbar bauen")
    pb.add_argument("--eval-n", type=int, default=10,
                    help="zurueckgehaltene Eval-Beispiele (Stride-gestreut)")
    pb.add_argument("--negative-ratio", type=float, default=0.3,
                    help="Anteil Negative am Trainingssatz (0 = keine)")
    pb.set_defaults(fn=cmd_build)

    pm = sub.add_parser("migrate", help="Alt-traces.jsonl ins neue Schema überführen")
    pm.add_argument("--source", default=str(Path.home() / "collect2" / "data" / "traces.jsonl"))
    pm.set_defaults(fn=cmd_migrate)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
