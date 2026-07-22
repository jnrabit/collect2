"""CLI-Entrypoint: collect-observe — Autopilot, SilentObserver, OsmosisObserver, DreamCycle.

USAGE:
  collect-observe start   Startet Autopilot + SilentObserver + OsmosisObserver
  collect-observe stop    Stoppt alle laufenden Beobachter
  collect-observe status  Status aller Beobachter
  collect-observe dream   DreamCycle einmalig ausführen (Vault-Mutation!)
  collect-observe analyze [type]  Log-Analyse (observer|osmosis)

Config via COLLECT_OBSERVE_* env / .env:
  COLLECT_OBSERVE_SILENT_INTERVAL=60    (default)
  COLLECT_OBSERVE_OSMOSIS_INTERVAL=30   (default)
  COLLECT_OBSERVE_AUTOPILOT_INTERVAL=30 (default)
  COLLECT_OBSERVE_AUTOPILOT_CYCLES=500  (0=endlos)
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from collect.config import settings
from collect.retrieval.service import RetrievalService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("collect-observe")

# ── Globale Referenzen für start/stop ─────────────────────────────────────────

_silent: object | None = None
_osmosis: object | None = None
_autopilot: object | None = None
_rs: RetrievalService | None = None
_shutdown = threading.Event()


def _build_rs() -> RetrievalService:
    from collect.retrieval.embedding import get_backend
    return RetrievalService(embedder=get_backend())


def cmd_start(args):
    global _silent, _osmosis, _autopilot, _rs, _shutdown
    _shutdown.clear()

    _rs = _build_rs()

    # ── SilentObserver ────────────────────────────────────────────────────────
    from collect.retrieval.silent_observer import SilentObserver
    _silent = SilentObserver(
        _rs.general.chaos,
        interval=settings.observe_silent_interval,
    )
    _silent.start()
    logger.info("SilentObserver läuft (Intervall=%ds)", settings.observe_silent_interval)

    # ── Autopilot ─────────────────────────────────────────────────────────────
    if settings.observe_autopilot_enabled:
        from collect.retrieval.autopilot import Autopilot
        _autopilot = Autopilot(
            _rs,
            interval=settings.observe_autopilot_interval,
            max_cycles=settings.observe_autopilot_cycles,
        )
        _autopilot.start()
        logger.info("Autopilot läuft (Intervall=%ds, max=%d)",
                    settings.observe_autopilot_interval,
                    settings.observe_autopilot_cycles)

    # ── OsmosisObserver ───────────────────────────────────────────────────────
    if settings.observe_osmosis_enabled:
        from collect.retrieval.osmosis_observer import OsmosisObserver
        _osmosis = OsmosisObserver(
            _rs.general.chaos,
            interval=settings.observe_osmosis_interval,
        )
        if _osmosis._initialized:
            _osmosis.start()
            logger.info("OsmosisObserver läuft (Intervall=%ds)",
                        settings.observe_osmosis_interval)
        else:
            logger.warning("OsmosisObserver: keine Cluster — deaktiviert")

    # Signal-Handler
    def _handle(sig, frame):
        logger.info("Signal %s — stoppe...", sig)
        _shutdown.set()
    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    logger.info("Beobachter aktiv. Ctrl+C zum Beenden.")
    try:
        while not _shutdown.is_set():
            time.sleep(5)
            if args.status_interval and _shutdown.wait(timeout=0):
                pass
    except KeyboardInterrupt:
        pass
    finally:
        cmd_stop(None)


def cmd_stop(_):
    global _silent, _osmosis, _autopilot
    if _autopilot:
        _autopilot.stop()
        _autopilot = None
    if _osmosis:
        _osmosis.stop()
        _osmosis = None
    if _silent:
        _silent.stop()
        _silent = None
    logger.info("Alle Beobachter gestoppt")


def cmd_status(_):
    parts = []
    if _silent:
        parts.append(f"SilentObserver: {json.dumps(_silent.status())}")
    else:
        from collect.retrieval.silent_observer import SilentObserver
        try:
            parts.append(f"SilentObserver: (nicht aktiv, log: {SilentObserver.__module__})")
        except Exception:
            parts.append("SilentObserver: nicht aktiv")

    if _autopilot:
        parts.append(f"Autopilot: {json.dumps(_autopilot.status())}")
    else:
        parts.append("Autopilot: nicht aktiv")

    if _osmosis:
        parts.append(f"OsmosisObserver: {json.dumps(_osmosis.status())}")
    else:
        parts.append("OsmosisObserver: nicht aktiv")

    print("\n".join(parts))


def cmd_dream(_):
    rs = _rs or _build_rs()
    from collect.retrieval.dream import DreamCycle
    dream = DreamCycle(rs.general)
    result = dream.run()
    print(f"Dream: {result.pruned} bereinigt, {result.crystals} Kristalle, "
          f"{result.skipped} übersprungen, {result.errors} Fehler")


def cmd_analyze(args):
    kind = args.kind or "observer"
    if kind == "observer":
        from collect.retrieval.silent_observer import analyze_log
        result = analyze_log()
    elif kind == "osmosis":
        from collect.retrieval.osmosis_observer import analyze_osmosis_log
        result = analyze_osmosis_log()
    else:
        print(f"Unbekannter Analyse-Typ: {kind}")
        sys.exit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="collect-observe — Beobachter-CLI")
    sub = parser.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="Startet alle Beobachter")
    p_start.add_argument("--status-interval", type=int, default=0,
                         help="Status-Intervall in Sekunden")

    sub.add_parser("stop", help="Stoppt alle Beobachter (in-process)")
    sub.add_parser("status", help="Status aller Beobachter")
    sub.add_parser("dream", help="DreamCycle einmalig ausführen")

    p_analyze = sub.add_parser("analyze", help="Log-Analyse")
    p_analyze.add_argument("kind", nargs="?", default="observer",
                           choices=["observer", "osmosis"],
                           help="Log-Typ: observer oder osmosis")

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "dream":
        cmd_dream(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
