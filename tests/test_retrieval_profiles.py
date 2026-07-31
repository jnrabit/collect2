"""Tests für Retrieval-Profile und Auto-Detection."""

import pytest

from collect.retrieval.profiles import (
    auto_detect,
    clean_query,
    parse_profile_override,
    resolve_profile,
)


def test_parse_profile_override_precise():
    assert parse_profile_override("[precise] Was ist TLS?") == ("Was ist TLS?", "precise")


def test_parse_profile_override_chaos():
    assert parse_profile_override("[chaos] entdecke neue konzepte") == ("entdecke neue konzepte", "chaos")


def test_parse_profile_override_no_prefix():
    assert parse_profile_override("Was ist TLS?") == ("Was ist TLS?", None)


def test_parse_profile_override_case_insensitive():
    assert parse_profile_override("[PRECISE] Test") == ("Test", "precise")
    assert parse_profile_override("[Chaos] Test") == ("Test", "chaos")


def test_parse_profile_override_invalid_prefix():
    q, p = parse_profile_override("[unknown] Frage")
    assert p is None
    assert q == "[unknown] Frage"


def test_clean_query_removes_profile():
    assert clean_query("[broad] suche mehr alternativen") == "suche mehr alternativen"


def test_clean_query_no_prefix():
    assert clean_query("normale frage") == "normale frage"


def test_auto_detect_precise():
    assert auto_detect("erkläre mir Quantencomputing") == "precise"
    assert auto_detect("was ist eine KI genau") == "precise"
    assert auto_detect("definiere den Begriff Entropie") == "precise"


def test_auto_detect_broad():
    assert auto_detect("gibt es noch andere Ansätze?") == "broad"
    assert auto_detect("liste alle alternativen auf") == "broad"
    assert auto_detect("welche weiteren methoden gibt es?") == "broad"


def test_auto_detect_chaos():
    assert auto_detect("entdecke neue ideen für") == "chaos"
    assert auto_detect("brainstorm zu kreativen konzepten") == "chaos"


def test_auto_detect_resonant():
    assert auto_detect("wie hängt das zusammen mit Quantenphysik?") == "resonant"
    assert auto_detect("warum unterscheiden sich die ansätze?") == "resonant"


def test_auto_detect_default_balanced():
    assert auto_detect("TLS Handshake") == "balanced"
    assert auto_detect("Python GIL") == "balanced"


def test_resolve_profile_override_wins():
    assert resolve_profile("[precise] was ist X?", "balanced") == "precise"


def test_resolve_profile_configured_wins():
    assert resolve_profile("normale frage", "chaos") == "chaos"


def test_resolve_profile_auto_detect():
    assert resolve_profile("erkläre mir das", "auto") == "precise"
    assert resolve_profile("normale frage", "auto") == "balanced"


def test_config_profiles_exist():
    from collect.config import settings
    profiles = settings.retrieval_profiles
    expected = {"precise", "balanced", "broad", "resonant", "chaos", "adaptive"}
    assert set(profiles.keys()) == expected
    for name, p in profiles.items():
        assert "alpha" in p
        assert "beta" in p
        assert "gamma" in p
        assert "delta" in p
        if name == "adaptive":
            continue  # Null-Gewichte — adaptiv zur Laufzeit
        total = p["alpha"] + p["beta"] + p["gamma"] + p["delta"]
        assert 0.99 <= total <= 1.01, f"{name}: Gewichte summieren nicht zu ~1.0"


def test_config_get_profile_invalid():
    from collect.config import settings
    p = settings.get_profile("nonexistent")
    assert p["alpha"] == 0.80  # balanced default


def test_resolve_profile_empty_query():
    from collect.config import settings
    result = resolve_profile("", settings.retrieval_profile)
    assert result == "chaos"  # default jetzt chaos statt auto


# ── Regression: "ist"/"was" dürfen NICHT precise kapern ──────────────────
# (Bug: bidirektionales Substring-Matching — "ist" ⊂ "was ist" → fast jeder
# deutsche Satz wurde precise. Regel-Priorität: chaos > broad > resonant >
# precise, Wort-Präfix statt Substring.)

def test_auto_detect_ist_does_not_hijack():
    assert auto_detect("warum ist der himmel blau") == "resonant"
    assert auto_detect("gibt es noch andere ansätze, was meinst du") == "broad"
    # "ist" allein ohne echtes Signal → balanced, nicht precise
    assert auto_detect("postgres ist auf port 5432") == "balanced"


def test_auto_detect_deterministic():
    # Keine set-Iteration mehr: gleiche Query → immer gleiches Profil
    q = "brainstorm: erkläre kreative alternativen im vergleich"
    assert len({auto_detect(q) for _ in range(50)}) == 1
    assert auto_detect(q) == "chaos"  # chaos hat höchste Priorität


# ── Orchestrator: Prefix wird GESTRIPPT + Profil wandert in den Request ──

def _run_orchestrator(query):
    from collect.agents.orchestrator import OrchestratorAgent
    from collect.bus import InMemoryBus, Message
    from collect.retrieval.router import CodeRouter
    from tests.conftest import FakeEmbedder

    bus = InMemoryBus(prefix="test.")
    emb = FakeEmbedder()
    OrchestratorAgent(bus, CodeRouter(emb.embed_one, centroid=None)).start()
    bus.publish("user_query", Message(
        type="user_query", data={"query": query}, correlation_id="p1"))
    return bus


def test_orchestrator_strips_profile_prefix():
    bus = _run_orchestrator("[precise] Was ist TLS?")
    ret = [m for ch, m in bus.published if ch == "retrieval_request"][0]
    llm = [m for ch, m in bus.published if ch == "llm_request"][0]
    # Der Prefix ist eine UI-Direktive — er darf NIRGENDS im Text landen
    assert "[precise]" not in ret.data["query"]
    assert all("[precise]" not in s for s in ret.data["subqueries"])
    assert "[precise]" not in llm.data["query"]
    assert ret.data["profile"]["alpha"] == 0.95  # precise-Gewichte aktiv


def test_orchestrator_passes_auto_detected_profile():
    bus = _run_orchestrator("TLS Handshake")
    ret = [m for ch, m in bus.published if ch == "retrieval_request"][0]
    # Default-Profil ist jetzt chaos → 0.50 alpha
    assert ret.data["profile"]["alpha"] == 0.50


# ── REPL: /retrieval set wirkt per Prefix (Client kann Service-Settings
# nicht mutieren — das Retrieval läuft im Service-Prozess) ────────────────

def test_repl_retrieval_set_stores_in_context():
    from collect.repl import cmd_retrieval
    ctx = {}
    out = cmd_retrieval("set broad", ctx)
    assert ctx["profile"] == "broad"
    assert "[broad]" in out
    out = cmd_retrieval("set auto", ctx)
    assert "profile" not in ctx
    assert cmd_retrieval("set nixda", ctx).startswith("✗")
