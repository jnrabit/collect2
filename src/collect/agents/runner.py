"""Runner — baut und startet den kompletten Agenten-Stack auf Redis.

Alle Agenten laufen in EINEM Prozess (teilen sich Embedding-Backend und
Vault-Stores im RAM); Parallelität kommt aus den Bus-Threads pro Agent.
Start: `collect-agents` (Entry-Point) oder python -m collect.agents.runner.
"""

from __future__ import annotations

import logging
import signal
import threading
import time

from collect.bus import Message, RedisBus
from collect.config import settings

logger = logging.getLogger(__name__)


def build_agents(bus, generate_fn=None):
    """Konstruiert alle Kern-Agenten (Ollama-generate injizierbar für Tests)."""
    from collect.agents.decision import DecisionAgent
    from collect.agents.executor import ExecutorAgent
    from collect.agents.learning import LearningAgent
    from collect.agents.llm import LLMAgent
    from collect.agents.orchestrator import OrchestratorAgent
    from collect.agents.planning import PlanningAgent
    from collect.agents.response import ResponseAgent
    from collect.agents.retrieval import RetrievalAgent
    from collect.grounding.facts import FactGrounder
    from collect.retrieval.embedding import get_backend
    from collect.retrieval.router import CodeRouter
    from collect.retrieval.service import VaultSearcher

    embedder = get_backend()
    router = CodeRouter.from_config(embedder.embed_one)
    grounder = FactGrounder(embedder.embed_one)

    translator = decomposer = None
    if settings.translate_enabled:
        from collect.retrieval.translator import QueryTranslator
        translator = QueryTranslator()
    if settings.decompose_enabled:
        from collect.retrieval.decomposer import QueryDecomposer
        decomposer = QueryDecomposer()

    logger.info("Lade Vaults …")
    general = VaultSearcher(settings.knowledge_vault_file,
                            settings.knowledge_cache_file,
                            settings.knowledge_field_file)
    code = VaultSearcher(settings.code_vault_file,
                         settings.code_cache_file,
                         settings.code_field_file)

    return [
        OrchestratorAgent(bus, router, translator, decomposer),
        RetrievalAgent(bus, general, embedder, kind="retrieval"),
        RetrievalAgent(bus, code, embedder, kind="code_retrieval",
                       trust_threshold=settings.code_vault_trust_threshold),
        LLMAgent(bus, generate_fn=generate_fn, grounder=grounder),
        DecisionAgent(bus, generate_fn=generate_fn),
        PlanningAgent(bus),
        ExecutorAgent(bus),
        ResponseAgent(bus),
        LearningAgent(bus),
    ]


def _start_heartbeat(bus, agents) -> threading.Thread:
    from collect.status import write_heartbeat

    def loop():
        while True:
            for agent in agents:
                bus.publish("heartbeat", Message(
                    type="heartbeat", data={"agent": agent.name, "ts": time.time()},
                    source=agent.name))
                write_heartbeat(bus.redis, agent.name)
            time.sleep(settings.heartbeat_interval)

    t = threading.Thread(target=loop, name="heartbeat", daemon=True)
    t.start()
    return t


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    bus = RedisBus()
    bus.redis.ping()
    agents = build_agents(bus)
    for agent in agents:
        agent.start()
    _start_heartbeat(bus, agents)
    logger.info("%d Agenten online (Prefix %r). Strg+C zum Beenden.",
                len(agents), settings.channel_prefix)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    bus.stop()
    logger.info("Beendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
