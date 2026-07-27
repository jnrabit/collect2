"""DreamCycle: Widerspruchs-Parsing und Apoptose.

Regression: `_load_contradictions` benutzte json ohne Import (c7cd456) —
jede nicht-leere distillation_data.jsonl ließ `collect-observe dream` mit
NameError sterben, bevor die Apoptose persistiert wurde.
"""

import json
import types

import pytest

from collect.retrieval.dream import META_SOURCE, DreamCycle


def _searcher(archive=None, resonance=None):
    """Minimaler VaultSearcher-Ersatz: nur .store und .field."""
    store = types.SimpleNamespace(archive=list(archive or []), _doc_index={})
    field = None
    if resonance is not None:
        field = types.SimpleNamespace(R=resonance)
    return types.SimpleNamespace(store=store, field=field)


def _dream(tmp_path, lines, archive=None, resonance=None):
    dist = tmp_path / "distillation_data.jsonl"
    dist.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return DreamCycle(_searcher(archive, resonance), distillation_file=dist)


# ── Widerspruchs-Parsing ─────────────────────────────────────────────────

def test_load_contradictions_selects_by_hit_spread(tmp_path, monkeypatch):
    import collect.retrieval.dream as mod
    monkeypatch.setattr(mod.settings, "dream_min_hit_spread", 10.0)
    lines = [
        json.dumps({"hit_spread": 14.2, "query": "weit auseinander"}),
        json.dumps({"hit_spread": 6.6, "query": "einig"}),
        json.dumps({"hit_spread": 10.0, "query": "genau auf der Schwelle"}),
    ]
    got = _dream(tmp_path, lines)._load_contradictions()
    assert [e["query"] for e in got] == ["weit auseinander", "genau auf der Schwelle"]


def test_load_contradictions_ignores_entries_without_spread(tmp_path):
    """Altbestand (vor der Umstellung) und Queries mit <2 Treffern haben keine
    Messung — die dürfen nicht als Widerspruch durchgehen."""
    lines = [
        json.dumps({"zone": "FALLBACK", "query": "alter Eintrag"}),   # kein Feld
        json.dumps({"hit_spread": None, "query": "zu wenige Treffer"}),
        json.dumps({"hit_spread": "viel", "query": "kaputter Typ"}),
    ]
    assert _dream(tmp_path, lines)._load_contradictions() == []


def test_load_contradictions_no_longer_keys_on_fallback_zone(tmp_path):
    """Regression: zone=FALLBACK war das alte, strukturell unerreichbare
    Kriterium. Es darf allein nichts mehr auslösen."""
    lines = [json.dumps({"zone": "FALLBACK", "hit_spread": 2.0, "query": "x"})]
    assert _dream(tmp_path, lines)._load_contradictions() == []


def test_load_contradictions_skips_malformed_lines(tmp_path):
    lines = [
        "{kein valides json",
        json.dumps({"hit_spread": 99.0, "query": "ok"}),
        "",
    ]
    got = _dream(tmp_path, lines)._load_contradictions()
    assert len(got) == 1 and got[0]["query"] == "ok"


def test_load_contradictions_empty_without_file(tmp_path):
    dream = DreamCycle(_searcher(), distillation_file=tmp_path / "fehlt.jsonl")
    assert dream._load_contradictions() == []


# ── Apoptose ─────────────────────────────────────────────────────────────

def _meta(i, generation=1):
    return {"id": f"META-{i}", "source": META_SOURCE, "generation": generation,
            "content": "meta", "title": f"Meta {i}"}


def _normal(i):
    return {"id": f"DOC-{i}", "source": "WIKIPEDIA", "content": "text",
            "title": f"Doc {i}"}


def test_apoptose_removes_only_unreferenced_meta_nodes(tmp_path):
    archive = [_normal(0), _meta(1), _meta(2), _normal(3)]
    # META-1 hängt im Resonanzfeld, META-2 nicht
    dream = _dream(tmp_path, [], archive=archive, resonance={"META-1": ["DOC-0"]})

    pruned = dream._prune_dead_meta_nodes(dream._collect_active_resonance_ids())

    assert pruned == 1
    assert {d["id"] for d in dream._store.archive} == {"DOC-0", "META-1", "DOC-3"}
    # Index wurde mitgezogen
    assert set(dream._store._doc_index) == {"DOC-0", "META-1", "DOC-3"}


def test_apoptose_spares_generation_zero_and_fremde_quellen(tmp_path):
    archive = [_normal(0), _meta(1, generation=0),
               {"id": "X", "source": "WIKIPEDIA", "generation": 3, "content": "c"}]
    dream = _dream(tmp_path, [], archive=archive, resonance={})
    assert dream._prune_dead_meta_nodes(set()) == 0
    assert len(dream._store.archive) == 3


def test_apoptose_without_resonance_field_is_noop_on_ids(tmp_path):
    """Kein Feld → keine aktiven IDs. Der Aufrufer darf den Vault nicht leeren,
    ohne dass das sichtbar wird."""
    dream = _dream(tmp_path, [], archive=[_normal(0)], resonance=None)
    assert dream._collect_active_resonance_ids() == set()


# ── Zusammenspiel: der Pfad, der vorher gecrasht ist ─────────────────────

def test_run_survives_nonempty_distillation_file(tmp_path):
    """run() bis zur Synthese: darf nicht mehr an fehlendem json-Import sterben."""
    lines = [json.dumps({"hit_spread": 1.0, "query": "kein Widerspruch"})]
    archive = [_normal(0), _meta(1)]
    dream = _dream(tmp_path, lines, archive=archive, resonance={})

    persisted = {"n": 0}
    dream._persist = lambda: persisted.__setitem__("n", persisted["n"] + 1)

    result = dream.run()
    assert result.pruned == 1
    assert persisted["n"] == 1        # Apoptose wird jetzt auch geschrieben
