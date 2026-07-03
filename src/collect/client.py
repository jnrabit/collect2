"""Client — Anfrage an den laufenden Agenten-Stack stellen.

CLI: collect-ask "Wie funktioniert TLS?"
Programmatisch: ask("…") → dict mit text/meta.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from collect.bus import Message, new_id
from collect.config import settings


def ask(query: str, timeout: float | None = None, show_progress: bool = False) -> dict:
    import redis as redis_lib

    timeout = timeout or settings.query_timeout
    r = redis_lib.Redis(host=settings.redis_host, port=settings.redis_port,
                        db=settings.redis_db, decode_responses=True)
    cid = new_id()
    prefix = settings.channel_prefix
    reply_channel = f"user_response.{cid}"

    pubsub = r.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(f"{prefix}{reply_channel}", f"{prefix}progress")
    try:
        r.publish(f"{prefix}user_query", Message(
            type="user_query", data={"query": query},
            correlation_id=cid, reply_to=reply_channel, source="client").to_json())

        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = pubsub.get_message(timeout=1.0)
            if raw is None:
                continue
            try:
                msg = json.loads(raw["data"])
            except (ValueError, TypeError):
                continue
            if msg.get("correlation_id") != cid:
                continue
            if msg.get("type") == "progress":
                if show_progress:
                    d = msg.get("data", {})
                    print(f"  ⏳ {d.get('stage', '?')}: {d.get('detail', '')}",
                          file=sys.stderr)
                continue
            if msg.get("type") == "user_response":
                return msg.get("data", {})
        return {"text": f"⚠️ Timeout nach {timeout:.0f}s — keine Antwort.",
                "meta": {"timeout": True}}
    finally:
        pubsub.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Anfrage an den collect-Agenten-Stack")
    ap.add_argument("query", help="Die Frage/Aufgabe")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--quiet", action="store_true", help="kein Progress auf stderr")
    ap.add_argument("--json", action="store_true", help="Rohantwort als JSON")
    args = ap.parse_args()

    result = ask(args.query, timeout=args.timeout, show_progress=not args.quiet)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result.get("text", ""))
        meta = result.get("meta", {})
        if meta:
            print(f"\n[zone={meta.get('zone', '?')} "
                  f"distance={meta.get('best_distance', '?')} "
                  f"dauer={meta.get('duration_s', '?')}s "
                  f"grund={meta.get('finalize_reason', '?')}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
