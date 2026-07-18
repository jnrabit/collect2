"""Eval-Framework: Messung, Baseline-Roundtrip, Regressions-Erkennung.

Der Fake-Service liefert kontrollierte Ergebnisse — die Logik (Zonen-Urteil,
Relevanz, Regression) wird ohne echte Vaults getestet.
"""

import json

import pytest

from collect import eval as ev
from collect.retrieval.service import RetrievalResult, VaultHit, VaultResult
from collect.retrieval.zones import classify_zone


class FakeService:
    """retrieve() liefert pro Query konfigurierte (distance, titel)-Antworten."""

    def __init__(self, answers: dict):
        self.answers = answers  # query → (best_distance, [titel, …])
        self.seen_profiles = []

    def retrieve(self, query, top_k=30, max_hits=5, profile=None):
        self.seen_profiles.append(profile)
        dist, titles = self.answers.get(query, (999.0, []))
        hits = [VaultHit(doc_id=f"d{i}", distance=dist + i, title=t,
                         content=t.lower()) for i, t in enumerate(titles)]
        vr = VaultResult(hits=hits)
        vr.verdict = classify_zone(dist if hits else None)
        return RetrievalResult(query=query, effective_query=query,
                               subqueries=[query], route="general",
                               route_score=0.1, general=vr)


QUERIES = [
    {"query": "tls frage", "route": "*", "answerable": True, "terms": ["tls"]},
    {"query": "nonsens blorp", "route": "*", "answerable": False, "terms": []},
]


def _good_service():
    return FakeService({
        "tls frage": (30.0, ["TLS Handshake Protocol", "Transport Security"]),
        "nonsens blorp": (999.0, []),
    })


# ── Messung ───────────────────────────────────────────────────────────────

def test_run_eval_good_case():
    results = ev.run_eval(_good_service(), QUERIES, ["basis"], repeats=2)
    r_tls, r_non = results["basis"]
    assert r_tls["zone_worst"] == "TRUST" and r_tls["relevant_ok"] is True
    assert r_tls["answerable_ok"] is True and r_tls["zone_stable"]
    assert r_non["zone_worst"] == "FALLBACK" and r_non["answerable_ok"] is True
    s = ev.summarize(results["basis"])
    assert s["relevant"] == "1/1" and s["answerable_ok"] == "2/2"


def test_run_eval_passes_profile():
    svc = _good_service()
    ev.run_eval(svc, QUERIES[:1], ["basis", "precise"], repeats=1)
    assert svc.seen_profiles[0] is None                 # basis = kein Profil
    assert svc.seen_profiles[1]["alpha"] == 0.95        # precise-Gewichte


def test_nonsense_reaching_trust_fails_answerable():
    svc = FakeService({"nonsens blorp": (20.0, ["Zufallstreffer"]),
                       "tls frage": (30.0, ["TLS Handshake"])})
    rows = ev.run_eval(svc, QUERIES, ["basis"], 1)["basis"]
    assert rows[1]["answerable_ok"] is False  # Nonsens darf nie TRUST erreichen


def test_missing_terms_fails_relevant():
    svc = FakeService({"tls frage": (30.0, ["Komplett anderes Thema"]),
                       "nonsens blorp": (999.0, [])})
    rows = ev.run_eval(svc, QUERIES, ["basis"], 1)["basis"]
    assert rows[0]["relevant_ok"] is False


# ── Baseline & Regression ────────────────────────────────────────────────

def _baseline_from(rows):
    return {"written": "2026-07-18", "repeats": 1, "rows": rows}


def test_no_regression_on_identical():
    rows = ev.run_eval(_good_service(), QUERIES, ["basis"], 1)["basis"]
    reg, warn = ev.check_regression(rows, _baseline_from(rows))
    assert reg == [] and warn == []


def test_zone_regression_detected():
    good = ev.run_eval(_good_service(), QUERIES, ["basis"], 1)["basis"]
    bad_svc = FakeService({"tls frage": (55.0, ["TLS Handshake"]),  # GRAUZONE
                           "nonsens blorp": (999.0, [])})
    bad = ev.run_eval(bad_svc, QUERIES, ["basis"], 1)["basis"]
    reg, _ = ev.check_regression(bad, _baseline_from(good))
    assert any("ZONE TRUST → GRAUZONE" in r for r in reg)


def test_relevance_loss_detected():
    good = ev.run_eval(_good_service(), QUERIES, ["basis"], 1)["basis"]
    bad_svc = FakeService({"tls frage": (30.0, ["Anderes Thema"]),
                           "nonsens blorp": (999.0, [])})
    bad = ev.run_eval(bad_svc, QUERIES, ["basis"], 1)["basis"]
    reg, _ = ev.check_regression(bad, _baseline_from(good))
    assert any("RELEVANZ" in r for r in reg)


def test_distance_drift_is_warning_not_regression():
    good = ev.run_eval(_good_service(), QUERIES, ["basis"], 1)["basis"]
    drift_svc = FakeService({"tls frage": (42.0, ["TLS Handshake"]),  # +12, TRUST
                             "nonsens blorp": (999.0, [])})
    drifted = ev.run_eval(drift_svc, QUERIES, ["basis"], 1)["basis"]
    reg, warn = ev.check_regression(drifted, _baseline_from(good))
    assert reg == []
    assert any("Distanz +12" in w for w in warn)


def test_baseline_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "baseline_path", lambda: tmp_path / "b.json")
    rows = ev.run_eval(_good_service(), QUERIES, ["basis"], 1)["basis"]
    path = ev.write_baseline(rows, repeats=1)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["rows"] == rows and "weights" in loaded
    reg, warn = ev.check_regression(rows, loaded)
    assert reg == [] and warn == []


# ── Query-Set-Override ───────────────────────────────────────────────────

def test_load_queries_default_and_override(tmp_path):
    assert ev.load_queries(None) is ev.EVAL_QUERIES
    f = tmp_path / "q.json"
    f.write_text(json.dumps([{"query": "eigene frage", "terms": ["x"]}]),
                 encoding="utf-8")
    qs = ev.load_queries(str(f))
    assert qs[0]["query"] == "eigene frage"
    assert qs[0]["route"] == "*" and qs[0]["answerable"] is None  # Defaults

    f.write_text(json.dumps([{"terms": []}]), encoding="utf-8")
    with pytest.raises(SystemExit):
        ev.load_queries(str(f))


def test_render_smoke():
    results = ev.run_eval(_good_service(), QUERIES, ["basis", "precise"], 1)
    out = ev.render(results, repeats=1)
    assert "tls frage" in out and "basis" in out and "precise" in out


# ── REPL-Debug-Ansicht ───────────────────────────────────────────────────

def test_format_vault_hits():
    from collect.repl import format_vault_hits
    out = format_vault_hits({
        "zone": "GRAUZONE", "best_distance": 55.3,
        "hits": [{"distance": 55.3, "title": "Ein Treffer",
                  "content": "Inhalt\nmit Umbruch"}]})
    assert "GRAUZONE" in out and "55.3" in out
    assert "Ein Treffer" in out and "Inhalt mit Umbruch" in out
