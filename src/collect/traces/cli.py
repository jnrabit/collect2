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
    print(json.dumps(get_collector().stats(), ensure_ascii=False, indent=2))
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
    result = curate_interactive(traces, curated_dir / "curation.jsonl")
    print(f"\nKuriert: {result['kept']} gut, {result['skipped']} übersprungen")
    if args.negatives:
        negs = build_negatives(traces)
        neg_path = curated_dir / f"{date.today().isoformat()}_synthetic.jsonl"
        with open(neg_path, "a", encoding="utf-8") as f:
            for n in negs:
                f.write(json.dumps(n, ensure_ascii=False) + "\n")
        print(f"Negativbeispiele erzeugt: {len(negs)} → {neg_path}")
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

    sub.add_parser("stats", help="Zählung der gesammelten Traces").set_defaults(fn=cmd_stats)

    pv = sub.add_parser("validate", help="Traces durchs echte Template prüfen")
    pv.add_argument("--tokenizer", default=None)
    pv.add_argument("--step-kind", default=None,
                    help="nur diese Kategorie prüfen (z. B. rewrite)")
    pv.set_defaults(fn=cmd_validate)

    pc = sub.add_parser("curate", help="Interaktive Kuration + Negativ-Synthese")
    pc.add_argument("--negatives", action="store_true", help="auch Negativbeispiele erzeugen")
    pc.set_defaults(fn=cmd_curate)

    pm = sub.add_parser("migrate", help="Alt-traces.jsonl ins neue Schema überführen")
    pm.add_argument("--source", default=str(Path.home() / "collect2" / "data" / "traces.jsonl"))
    pm.set_defaults(fn=cmd_migrate)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
