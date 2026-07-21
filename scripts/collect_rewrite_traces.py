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
BATCH2: list[tuple[str, str]] = [
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

# Charge 3 (47 Paare) — bis 100 Rewrite-Traces. Bewusst breiter als 1+2:
# die waren fast reines Infra/ML. Ein Rewriter, der nur Fachbegriffe seiner
# Trainingsdomaene aufloest, ist ueberangepasst; deshalb hier gut die Haelfte
# ausserhalb (Alltag, Recht, Biologie, Handwerk, Finanzen, Geschichte).
BATCH3: list[tuple[str, str]] = [
    # A · Technik, Pronomen/Ellipse
    ("Was ist ein Write-Ahead-Log?", "und wann wird das geleert?"),
    ("Wie funktioniert ein Circuit Breaker im Microservice?", "wann schliesst er wieder?"),
    ("Was macht ein Reverse Proxy?", "und wofuer braucht man den?"),
    ("Wie arbeitet ein LRU-Cache?", "wie schnell ist das bei einem Treffer?"),
    ("Was ist Eventual Consistency?", "und wie lange dauert das typischerweise?"),
    ("Wie funktioniert Zero-Downtime-Deployment?", "und bei Datenbank-Migrationen?"),
    ("Was macht ein Load Balancer mit Sticky Sessions?", "welche Nachteile hat das?"),
    ("Wie funktioniert OCR bei gescannten Dokumenten?", "und bei Handschrift?"),
    ("Was ist ein Deadlock in der Datenbank?", "wie erkennt man das?"),
    ("Wie arbeitet Rsync beim Abgleich?", "und ueber langsame Leitungen?"),
    ("Was macht ein Debugger mit Breakpoints?", "und bei optimiertem Code?"),
    ("Wie funktioniert Content-Addressable Storage?", "und beim Loeschen?"),
    ("Was ist Property-Based Testing?", "wie findet das Gegenbeispiele?"),
    ("Wie arbeitet ein Just-in-Time-Compiler?", "wann lohnt sich das nicht?"),
    ("Was macht ein Bloom-Filter bei Kollisionen?", "und wie stellt man das ein?"),
    ("Wie funktioniert Log-Rotation unter Linux?", "und bei laufenden Prozessen?"),
    # B · Alltag / Handwerk
    ("Wie funktioniert eine Waermepumpe?", "und bei Frost?"),
    ("Was macht Sauerteig beim Brotbacken?", "wie lange muss das gehen?"),
    ("Wie entsteht Kondenswasser am Fenster?", "und was hilft dagegen?"),
    ("Wie funktioniert ein Dreiwegeventil in der Heizung?", "wann schaltet das um?"),
    ("Was bewirkt Anlassen von Stahl?", "bei welcher Temperatur macht man das?"),
    ("Wie funktioniert ein Schuko-Stecker mit Schutzleiter?", "und im Ausland?"),
    ("Warum rostet Aluminium nicht wie Eisen?", "und im Salzwasser?"),
    ("Wie funktioniert eine Zentrifuge beim Waescheschleudern?", "warum wird das nicht ganz trocken?"),
    # C · Biologie / Medizin
    ("Was macht Insulin im Koerper?", "was passiert wenn das fehlt?"),
    ("Wie funktioniert die Blut-Hirn-Schranke?", "und welche Stoffe kommen da durch?"),
    ("Was ist ein Antikoerper?", "wie lange haelt das?"),
    ("Wie arbeiten Mitochondrien in der Zelle?", "und woher kommen die urspruenglich?"),
    ("Was macht Chlorophyll bei der Photosynthese?", "warum ist das gruen?"),
    ("Wie funktioniert ein Nervenimpuls entlang des Axons?", "wie schnell ist das?"),
    ("Was bewirkt Koffein im Gehirn?", "und warum gewoehnt man sich daran?"),
    # D · Recht / Verwaltung / Finanzen
    ("Was regelt die Impressumspflicht?", "gilt das auch fuer private Blogs?"),
    ("Wie funktioniert das Widerrufsrecht beim Onlinekauf?", "und bei Software?"),
    ("Was ist eine Patronatserklaerung?", "wann ist das bindend?"),
    ("Wie funktioniert die degressive Abschreibung?", "und bei gebrauchten Anlagen?"),
    ("Was macht die Grundschuld im Grundbuch?", "wie wird das geloescht?"),
    ("Wie funktioniert ein Sperrminoritaets-Anteil?", "ab welchem Prozentsatz greift das?"),
    ("Was bedeutet Verjaehrung bei einer Forderung?", "wann faengt das an zu laufen?"),
    ("Wie funktioniert ein ETF-Sparplan?", "und bei fallenden Kursen?"),
    # E · Geschichte / Gesellschaft
    ("Was war der Marshallplan?", "wer hat davon profitiert?"),
    ("Wie funktionierte das Zunftwesen im Mittelalter?", "und wer durfte da nicht rein?"),
    ("Was bewirkte die Erfindung des Buchdrucks?", "wie schnell verbreitete sich das?"),
    ("Wie kam es zur Hanse?", "woran ist das zerbrochen?"),
    # F · Englisch (Spracherhalt pruefen)
    ("How does a bank run happen?", "and how do central banks stop it?"),
    ("What is photosynthesis in simple terms?", "and what happens at night?"),
    ("How does noise-cancelling in headphones work?", "why does it fail on voices?"),
    ("What does a patent actually protect?", "how long does it last?"),
]

BATCHES = {"2": BATCH2, "3": BATCH3, "all": BATCH2 + BATCH3}


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
    ap.add_argument("--batch", default="3", choices=sorted(BATCHES),
                    help="welche Charge fahren")
    args = ap.parse_args()

    if not settings.traces_enabled:
        print("traces_enabled=False — es würde nichts aufgezeichnet.", file=sys.stderr)
        return 1

    pairs = BATCHES[args.batch]
    gated = [f for _, f in pairs if not is_referential(f)]
    print(f"Charge {args.batch}: {len(pairs)} Paare, "
          f"{len(gated)} davon vom Gate abgewiesen:")
    for f in gated:
        print(f"  gated: {f}")
    if args.dry_run:
        return 0

    ok = fail = skipped = 0
    t0 = time.time()
    for i, (base, follow) in enumerate(pairs):
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
