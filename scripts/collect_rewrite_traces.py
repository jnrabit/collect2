#!/usr/bin/env python3
"""Rewrite-Traces batchweise sammeln (Charge aus trace_batch_2.md).

Fährt echte Paare (Basisfrage → Folgefrage) durch den echten Rewriter: die
Basisfrage wird vom Modell beantwortet, die Antwort landet als Historie, die
Folgefrage geht durch dasselbe deterministische Gate und dieselbe Prompt-
Pipeline wie im REPL. Der Trace entsteht im Rewriter selbst — nichts an der
Aufzeichnung ist hier nachgebaut.

Gate-Hinweis: schon eigenständige Folgefragen kommen NICHT durch
is_referential() und erzeugen daher keinen Trace (UNCHANGED-Fälle bleiben
synthetisch). Das Skript meldet solche Paare als "gated".

Aufruf:  .venv/bin/python scripts/collect_rewrite_traces.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import time

from collect.agents import ollama
from collect.config import settings
from collect.retrieval.rewriter import is_referential, rewrite

# (Basisfrage, Folgefrage) — Gruppen A–D und F aus trace_batch_2.md.
# Gruppe E (schon eigenständig) fehlt bewusst: siehe Gate-Hinweis oben.
PAIRS: list[tuple[str, str]] = [
    # A · Pronomen-Referenz
    ("Was macht ein Merkle-Tree?", "und wo wird das eingesetzt?"),
    ("Wie funktioniert ein B-Tree-Index in PostgreSQL?", "wann bringt das nichts?"),
    ("Wofür ist ein JWT gedacht?", "wie lange sollte es gültig sein?"),
    ("Was besagt das CAP-Theorem?", "welchen Teil davon geben Datenbanken meist auf?"),
    ("Wie arbeitet der Garbage Collector in Java?", "wann pausiert er die Anwendung?"),
    ("Was macht der Borrow-Checker in Rust?", "warum ist er am Anfang so sperrig?"),
    ("Wie funktioniert DNS-Auflösung?", "wie lange wird das zwischengespeichert?"),
    ("Was ist ein CRDT?", "wie löst das Konflikte auf?"),
    # B · Ellipse ohne Pronomen
    ("Wie funktioniert Kafka-Partitionierung?", "und bei nur einem Consumer?"),
    ("Was bringt mmap beim Lesen großer Dateien?", "und bei Schreibzugriffen?"),
    ("Wie arbeitet der Raft-Konsens-Algorithmus?", "und wenn der Leader ausfällt?"),
    ("Wozu dient Backpressure in Datenströmen?", "und ohne?"),
    ("Was macht BPE bei der Tokenisierung?", "und bei Umlauten?"),
    ("Wie funktioniert Chunking beim RAG?", "und bei Tabellen?"),
    ("Was macht Dropout beim Training?", "und zur Inferenzzeit?"),
    # C · Vergleich / Alternative
    ("Wie funktioniert HNSW als Vektorindex?", "und wie schlägt sich das gegen IVF-Flat?"),
    ("Was macht Adam als Optimierer besser als SGD?", "und bei kleinen Batches?"),
    ("Wie arbeitet btrfs mit Copy-on-Write?", "und wie macht ext4 das?"),
    ("Was ist Beam-Search bei der Textgenerierung?", "worin unterscheidet es sich von Greedy?"),
    ("Wie funktioniert OAuth2 mit Authorization Code Flow?", "und wie läuft das implizit?"),
    ("Was macht FlashAttention schneller?", "und wie steht das zur normalen Attention?"),
    # D · Auswahl / Rückbezug auf Aufzählung
    ("Welche Isolationsstufen kennt SQL?", "welche davon verhindert Phantom-Reads?"),
    ("Welche Docker-Storage-Driver gibt es?", "welcher davon ist heute Standard?"),
    ("Welche Kompressionsverfahren nutzt HTTP?", "welches davon spart am meisten?"),
    ("Welche Speculative-Decoding-Varianten gibt es?", "welche davon braucht kein zweites Modell?"),
    ("Welche systemd-Unit-Typen gibt es?", "welcher davon startet beim Booten?"),
    # F · Englisch (Sprache muss erhalten bleiben)
    ("How does copy-on-write forking work?", "and what about shared memory?"),
    ("What is the Python GIL?", "why does it hurt CPU-bound code?"),
    ("How does a WebSocket handshake work?", "and how is it kept alive?"),
    ("What does a regex backtracking engine do?", "when does it blow up?"),
    ("How does gradient accumulation work?", "and how does that change the batch size?"),
]


def answer(question: str, model: str) -> str:
    """Kurze Antwort auf die Basisfrage — sie ist die Historie, die der
    Rewriter sieht, nicht das Trainingsziel. Deshalb bewusst knapp."""
    return ollama.generate(
        question, system="Antworte in zwei kurzen deutschen Sätzen.",
        model=model, timeout=60.0, temperature=0.0).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="nur zeigen, welche Folgefragen durchs Gate kämen")
    ap.add_argument("--model", default=settings.decompose_model)
    ap.add_argument("--start", type=int, default=0, help="ab diesem Paar (Wiederaufnahme)")
    args = ap.parse_args()

    if not settings.traces_enabled:
        print("traces_enabled=False — es würde nichts aufgezeichnet.", file=sys.stderr)
        return 1

    gated = [f for _, f in PAIRS if not is_referential(f)]
    print(f"{len(PAIRS)} Paare, {len(gated)} davon vom Gate abgewiesen:")
    for f in gated:
        print(f"  gated: {f}")
    if args.dry_run:
        return 0

    ok = fail = skipped = 0
    t0 = time.time()
    for i, (base, follow) in enumerate(PAIRS):
        if i < args.start:
            continue
        if not is_referential(follow):
            skipped += 1
            continue
        try:
            hist = [{"q": base, "a": answer(base, args.model)}]
            res = rewrite(follow, hist)
        except Exception as e:  # noqa: BLE001 — ein Ausfall darf die Charge nicht killen
            print(f"[{i:2d}] FEHLER {follow!r}: {e}", flush=True)
            fail += 1
            continue
        mark = "✓" if res["applied"] else "·"
        print(f"[{i:2d}] {mark} {follow!r} → {res['rewritten']!r}", flush=True)
        ok += 1
    print(f"\n{ok} verarbeitet, {skipped} gated, {fail} Fehler "
          f"({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
