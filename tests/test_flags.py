"""Tests für die Fehler-Inventur (Flags, Aufhebung, Validierung, Aggregation).

Kein Modell, kein Netz — reine Logik gegen ein temporäres Verzeichnis.
"""

import json

import pytest

from collect.traces import flags as F
from collect.traces.review import render_trace, review_loop


def _trace(tid, kind="rewrite", target="Wie funktioniert X?", model=None):
    meta = {"trace_id": tid, "step_kind": kind, "timestamp": "2026-07-29T10:00:00"}
    if model:
        meta["model"] = model
    return {"messages": [{"role": "system", "content": "sys"},
                         {"role": "user", "content": "frage"},
                         {"role": "assistant", "content": target}],
            "tools": [], "meta": meta}


# ── Schreiben / Validierung ──────────────────────────────────────────────

def test_write_flag_ist_append_only(tmp_path):
    F.write_flag("t1", "register", base_dir=tmp_path)
    F.write_flag("t1", "format", base_dir=tmp_path)
    rows = F.load_flags(tmp_path)
    assert [r["klasse"] for r in rows] == ["register", "format"]
    # zwei Zeilen, nichts ueberschrieben
    assert len(F.flags_path(tmp_path).read_text().strip().splitlines()) == 2


def test_mehrfach_flags_pro_trace(tmp_path):
    """Ein Schritt kann zwei Fehler haben."""
    F.write_flag("t1", "register", base_dir=tmp_path)
    F.write_flag("t1", "format", base_dir=tmp_path)
    assert F.effective(F.load_flags(tmp_path))["t1"] == {"register", "format"}


def test_none_hebt_vorherige_flags_auf(tmp_path):
    """Korrekturpfad ohne Loeschen — die alten Zeilen bleiben stehen."""
    F.write_flag("t1", "register", base_dir=tmp_path)
    F.write_flag("t1", "none", base_dir=tmp_path)
    eff = F.effective(F.load_flags(tmp_path))
    assert eff["t1"] == set()
    assert len(F.load_flags(tmp_path)) == 2  # Historie erhalten


def test_flag_nach_none_wirkt_wieder(tmp_path):
    F.write_flag("t1", "register", base_dir=tmp_path)
    F.write_flag("t1", "none", base_dir=tmp_path)
    F.write_flag("t1", "konfabulation", base_dir=tmp_path)
    assert F.effective(F.load_flags(tmp_path))["t1"] == {"konfabulation"}


def test_unbekannte_klasse_nennt_gueltige(tmp_path):
    with pytest.raises(F.UnbekannteKlasse) as e:
        F.write_flag("t1", "quatsch", base_dir=tmp_path)
    assert "register" in str(e.value)  # Liste der gueltigen Klassen dabei


def test_sonstiges_verlangt_note(tmp_path):
    with pytest.raises(F.UnbekannteKlasse):
        F.write_flag("t1", "sonstiges", base_dir=tmp_path)
    F.write_flag("t1", "sonstiges", note="etwas Neues", base_dir=tmp_path)
    assert F.effective(F.load_flags(tmp_path))["t1"] == {"sonstiges"}


def test_kaputte_zeile_wird_uebersprungen(tmp_path):
    F.write_flag("t1", "register", base_dir=tmp_path)
    with open(F.flags_path(tmp_path), "a", encoding="utf-8") as f:
        f.write("{kaputt\n")
    assert len(F.load_flags(tmp_path)) == 1


# ── Aggregation ──────────────────────────────────────────────────────────

def test_aggregate_quote_bezieht_sich_auf_gesichtete(tmp_path):
    traces = [_trace(f"t{i}") for i in range(10)]
    F.write_flag("t0", "register", base_dir=tmp_path)
    F.write_flag("t1", "none", base_dir=tmp_path)      # gesichtet, korrekt
    agg = F.aggregate(traces, F.load_flags(tmp_path))
    assert agg["total"] == 10
    assert agg["gesichtet"] == 2          # nur zwei angesehen
    assert agg["fehlerhaft"] == 1
    assert agg["korrekt"] == 1
    assert agg["fehlerquote_prozent"] == 50.0   # 1/2, NICHT 1/10


def test_aggregate_zaehlt_beide_klassen_eines_traces(tmp_path):
    traces = [_trace("t1")]
    F.write_flag("t1", "register", base_dir=tmp_path)
    F.write_flag("t1", "format", base_dir=tmp_path)
    agg = F.aggregate(traces, F.load_flags(tmp_path))
    assert agg["fehlerhaft"] == 1                     # ein Trace
    assert {k["klasse"] for k in agg["klassen"]} == {"register", "format"}


def test_aggregate_bringt_quellen_spalte(tmp_path):
    traces = [_trace("t1")]
    F.write_flag("t1", "konfabulation", base_dir=tmp_path)
    agg = F.aggregate(traces, F.load_flags(tmp_path))
    assert agg["klassen"][0]["quelle"] == "nur Handarbeit"


def test_aggregate_trennt_step_kind_und_modell(tmp_path):
    traces = [_trace("t1", kind="rewrite", model="qwen2.5:3b"),
              _trace("t2", kind="answer", model="qwen3:8b")]
    F.write_flag("t1", "rewrite_falsch", base_dir=tmp_path)
    F.write_flag("t2", "none", base_dir=tmp_path)
    agg = F.aggregate(traces, F.load_flags(tmp_path))
    assert agg["nach_step_kind"]["rewrite"]["fehlerhaft"] == 1
    assert agg["nach_step_kind"]["answer"]["fehlerhaft"] == 0
    assert agg["nach_modell"]["qwen2.5:3b"]["fehlerhaft"] == 1
    assert agg["nach_modell"]["qwen3:8b"]["gesichtet"] == 1


def test_modell_unbekannt_wenn_nicht_mitgeschrieben(tmp_path):
    """Der Collector schreibt derzeit kein Modell mit — das muss sichtbar sein,
    nicht stillschweigend zusammengefasst werden."""
    agg = F.aggregate([_trace("t1")], [])
    assert "unbekannt" in next(iter(agg["nach_modell"]))


def test_ungesichtete_sind_die_ohne_jede_zeile(tmp_path):
    traces = [_trace("t1"), _trace("t2")]
    F.write_flag("t1", "none", base_dir=tmp_path)
    offen = F.ungesichtete(traces, F.load_flags(tmp_path))
    assert [t["meta"]["trace_id"] for t in offen] == ["t2"]


# ── Review-Loop (ohne Terminal) ──────────────────────────────────────────

def test_review_enter_ist_korrekt_und_zaehlt_als_gesichtet(tmp_path):
    traces = [_trace("t1")]
    antworten = iter([""])
    z = review_loop(traces, [], base_dir=tmp_path,
                    eingabe=lambda _: next(antworten), ausgabe=lambda *_: None)
    assert z["korrekt"] == 1
    assert F.effective(F.load_flags(tmp_path))["t1"] == set()
    assert F.ungesichtete(traces, F.load_flags(tmp_path)) == []


def test_review_ziffer_waehlt_klasse(tmp_path):
    traces = [_trace("t1")]
    erste_klasse = list(F.FEHLERKLASSEN)[0]
    antworten = iter(["1"])
    review_loop(traces, [], base_dir=tmp_path,
                eingabe=lambda _: next(antworten), ausgabe=lambda *_: None)
    assert F.effective(F.load_flags(tmp_path))["t1"] == {erste_klasse}


def test_review_skip_bleibt_ungesichtet(tmp_path):
    traces = [_trace("t1")]
    antworten = iter(["s"])
    z = review_loop(traces, [], base_dir=tmp_path,
                    eingabe=lambda _: next(antworten), ausgabe=lambda *_: None)
    assert z["skip"] == 1
    assert F.load_flags(tmp_path) == []
    assert len(F.ungesichtete(traces, [])) == 1


def test_review_q_beendet_und_haelt_fortschritt(tmp_path):
    traces = [_trace("t1"), _trace("t2")]
    antworten = iter(["", "q"])
    z = review_loop(traces, [], base_dir=tmp_path,
                    eingabe=lambda _: next(antworten), ausgabe=lambda *_: None)
    assert z["gesichtet"] == 1                       # t1 gespeichert
    assert F.ungesichtete(traces, F.load_flags(tmp_path))[0]["meta"]["trace_id"] == "t2"


def test_review_sonstiges_erzwingt_notiz(tmp_path):
    traces = [_trace("t1")]
    idx = list(F.FEHLERKLASSEN).index("sonstiges") + 1
    antworten = iter([str(idx), "", str(idx), "echte Notiz"])
    review_loop(traces, [], base_dir=tmp_path,
                eingabe=lambda _: next(antworten), ausgabe=lambda *_: None)
    rows = F.load_flags(tmp_path)
    assert len(rows) == 1 and rows[0]["note"] == "echte Notiz"


def test_render_zeigt_target_voll_und_kuerzt_kontext():
    lang = "x" * 500
    t = _trace("t1", target="Das vollstaendige Ziel bleibt stehen")
    t["messages"][1]["content"] = lang
    out = render_trace(t, 1, 1)
    assert "Das vollstaendige Ziel bleibt stehen" in out
    assert lang not in out          # Kontext gekuerzt
    assert "…" in out


def test_render_formatiert_tool_calls():
    t = _trace("t1", target="")
    t["messages"][-1]["tool_calls"] = [
        {"function": {"name": "retrieve", "arguments": {"query": "RRF"}}}]
    assert "retrieve(" in render_trace(t)


# ── Collector-Abgrenzung ─────────────────────────────────────────────────

def test_flags_datei_wird_nicht_als_trace_gelesen(tmp_path):
    """flags.jsonl liegt im Trace-Ordner — load_all darf sie nicht einlesen."""
    from collect.traces.collector import TraceCollector
    c = TraceCollector(base_dir=tmp_path)
    c.record("rewrite", [{"role": "system", "content": "s"},
                         {"role": "user", "content": "u"},
                         {"role": "assistant", "content": "a"}])
    F.write_flag("irgendwas", "register", base_dir=tmp_path)
    assert len(c.load_all()) == 1


def test_korpusdefekt_zaehlt_nicht_in_die_fehlerquote(tmp_path):
    """Testfixtures im Bestand sind ein Korpus-, kein Modellfehler — sonst
    misst man die eigene Testdaten-Verschmutzung als Modellschwaeche."""
    traces = [_trace("t1"), _trace("t2")]
    F.write_flag("t1", "kein_trace", base_dir=tmp_path)
    F.write_flag("t2", "register", base_dir=tmp_path)
    agg = F.aggregate(traces, F.load_flags(tmp_path))
    assert agg["gesichtet"] == 2
    assert agg["fehlerhaft"] == 1                 # nur t2
    assert agg["fehlerquote_prozent"] == 50.0
    # ausgewiesen wird die Klasse trotzdem
    assert {k["klasse"] for k in agg["klassen"]} == {"kein_trace", "register"}


# ── Provenienz (Modell/Treiber/Quant) ────────────────────────────────────

def test_provenienz_schluessel_nennt_treiber_und_quant(tmp_path):
    """Modellname allein reicht nicht: derselbe Name ueber einen anderen
    Treiber ist ein anderes Ergebnis (17/24 fp16 vs 13/24 q4)."""
    t = _trace("t1")
    t["meta"].update({"model": "qwen3:8b", "driver": "ollama", "quant": "Q4_K_M"})
    F.write_flag("t1", "none", base_dir=tmp_path)
    agg = F.aggregate([t], F.load_flags(tmp_path))
    assert "qwen3:8b @ollama (Q4_K_M)" in agg["nach_modell"]


def test_altbestand_bleibt_unbekannt_statt_geraten(tmp_path):
    agg = F.aggregate([_trace("t1")], [])
    assert "unbekannt" in next(iter(agg["nach_modell"]))


def test_collector_schreibt_provenienz(tmp_path):
    from collect.traces.collector import TraceCollector
    c = TraceCollector(base_dir=tmp_path)
    c.record("rewrite", [{"role": "system", "content": "s"},
                         {"role": "user", "content": "u"},
                         {"role": "assistant", "content": "a"}],
             provenance={"model": "qwen3:8b", "driver": "ollama",
                         "quant": "Q4_K_M", "model_digest": "abc123"})
    m = c.load_all()[0]["meta"]
    assert (m["model"], m["driver"], m["quant"], m["model_digest"]) == \
           ("qwen3:8b", "ollama", "Q4_K_M", "abc123")


def test_fehlende_provenienz_ist_none_keine_exception(tmp_path):
    """Collector-Ausfall darf keinen Agenten-Aufruf brechen."""
    from collect.traces.collector import TraceCollector
    c = TraceCollector(base_dir=tmp_path)
    tid = c.record("rewrite", [{"role": "system", "content": "s"},
                               {"role": "user", "content": "u"},
                               {"role": "assistant", "content": "a"}])
    assert tid is not None
    m = c.load_all()[0]["meta"]
    assert m["model"] is None and m["driver"] is None
