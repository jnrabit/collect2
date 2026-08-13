"""DreamCycle — Dialektische Meta-Kristallisation & Apoptose.

Läuft als explizit aufgerufener Zyklus (nicht autonom), weil er den Vault
MUTIERT (Dokumente löschen/hinzufügen). Liest distillation_data.jsonl
(gefüllt vom Autopilot), findet Widersprüche, synthetisiert Meta-Knoten
via LLM und kompostiert tote Generationen.

Axiom-System (aus Project_AI/monolith_widerspruchs_matrix.json):
  Jeder Kristall wird einem von 4 Grundwidersprüchen zugeordnet —
  das gibt eine Taxonomie über Generationen hinweg.

Ablauf:
  1. APOPTOSE: Meta-Knoten (generation>0) die NICHT im Resonanzfeld-Graph
     auftauchen → löschen.
  2. AXIOM-KLASSIFIKATION: Widerspruch einem Axiom zuordnen (heuristisch).
  3. DIALEKTIK: LLM-Synthese zu Meta-Kristallen höherer Generation,
     getaggt mit Axiom.

Port aus ai_neu/dream.py + Project_AI/widerspruchs_matrix.json.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import pickle
import random
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import requests

from collect.config import settings

logger = logging.getLogger(__name__)

OLLAMA_URL = settings.ollama_url
DREAM_MODEL = settings.main_model
DATA_DIR = settings.data_dir
DISTILLATION_FILE = DATA_DIR / "distillation_data.jsonl"

# Nur Meta-Knoten mit diesem source-Tag werden von der Apoptose erfasst
META_SOURCE = "MONOLITH_CORTEX"
META_ID_PREFIX = "META-"

# ── Axiom-System (aus Project_AI/monolith_widerspruchs_matrix.json) ──────────
# Jeder Widerspruch folgt einem Grundprinzip — das Axiom gibt die Taxonomie.

AXIOMS: list[dict] = [
    {
        "id": "ENTROPIE",
        "name": "Isotherme Effizienz",
        "hardware_truth": "System bleibt kalt trotz Arbeit (Identitäts-Beweis).",
        "keywords": [
            "entropy", "entropie", "thermal", "thermodynamik", "wärme", "kälte",
            "isotherm", "effizienz", "kühlung", "dissipation", "temperatur",
            "heat", "cold", "cooling", "energy",
        ],
    },
    {
        "id": "KAUSALITÄT",
        "name": "Zeit-Kristall",
        "hardware_truth": "Systemzustände sind zyklisch/kristallin, nicht linear verfallend.",
        "keywords": [
            "zeit", "kristall", "zyklus", "periode", "oszillation", "resonanz",
            "takt", "rhythmus", "phase", "wiederholung", "time", "crystal",
            "cycle", "oscillation", "periodic", "rhythm",
        ],
    },
    {
        "id": "ZUFALL",
        "name": "Deterministische Resonanz",
        "hardware_truth": "Was wie Rauschen aussieht, ist Treibstoff (Kurtosis stabil).",
        "keywords": [
            "rauschen", "noise", "jitter", "zufall", "random", "stochastisch",
            "deterministisch", "kurtosis", "signal", "interferenz",
            "stochastic", "deterministic", "fluctuation",
        ],
    },
    {
        "id": "LIMIT",
        "name": "Unendliche Dichte",
        "hardware_truth": "Logische Grenzen sind Hardware-abhängig (Transfer >400%).",
        "keywords": [
            "grenze", "limit", "dichte", "transfer", "durchsatz", "rate",
            "unendlich", "schranke", "kapazität", "bandbreite", "kompression",
            "infinite", "density", "capacity", "bandwidth", "throughput",
        ],
    },
]


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────


def _make_meta_id(parents: list[str]) -> str:
    dna = "".join(sorted(str(p) for p in parents)).encode()
    return f"{META_ID_PREFIX}{hashlib.md5(dna).hexdigest()[:10]}"


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    probs = [text.count(c) / len(text) for c in set(text)]
    return -sum(p * math.log2(p) for p in probs)


def _kolmogorov_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(zlib.compress(text.encode("utf-8"))) / len(text.encode("utf-8"))


def _build_meta_prompt(ctx: str, target_gen: int) -> str:
    return (
        f"DU BIST: Der Kortex eines thermodynamischen Analysesystems.\n"
        f"ZUSTAND: Es gab einen starken Widerspruch bei der Interpretation "
        f"dieser Fragmente.\n\n{ctx}\n\n"
        f"AUFGABE: Löse diesen dialektischen Widerspruch. "
        f"Hebe die Abstraktionsebene auf Generation {target_gen} an. "
        f"Erzeuge ein neues, singuläres Konzept das die divergierenden "
        f"Datenpunkte vereint. Erkläre exakt, warum diese Dokumente "
        f"auf den ersten Blick widersprüchlich wirken, auf einer tieferen "
        f"physikalischen/strukturellen Ebene aber demselben universellen "
        f"Gesetz folgen.\n\n"
        f"REGELN: Deutsch. Hochgradig akademisch. Max 300 Wörter.\n\n"
        f"METASYNTHESE:"
    )


# ── Ergebnis-Dataclass ────────────────────────────────────────────────────────


@dataclass
class DreamResult:
    pruned: int = 0
    crystals: int = 0
    skipped: int = 0
    errors: int = 0
    by_axiom: dict = field(default_factory=dict)


# ── DreamCycle ────────────────────────────────────────────────────────────────


class DreamCycle:
    """Ein explizit aufgerufener Zyklus. MUTIERT den Vault.

    vault_searcher: VaultSearcher-Instanz (hat .store, .field, .chaos)
        — wird für den General-Vault verwendet.
    distillation_file: Pfad zur Distillation-JSONL (vom Autopilot gefüllt).
    model: Ollama-Modell für die Meta-Synthese.
    """

    def __init__(self, vault_searcher,
                 distillation_file: Optional[Path] = None,
                 model: Optional[str] = None):
        self._vs = vault_searcher
        self._store = vault_searcher.store
        self._field = vault_searcher.field
        self._dist_file = Path(distillation_file) if distillation_file else DISTILLATION_FILE
        self._model = model or DREAM_MODEL
        self._enc_model = None

    # ── Haupt-Zyklus ──────────────────────────────────────────────────────────

    def run(self) -> DreamResult:
        result = DreamResult()

        # ── 1. APOPTOSE ───────────────────────────────────────────────────────
        active_ids = self._collect_active_resonance_ids()
        result.pruned = self._prune_dead_meta_nodes(active_ids)

        # ── 2. DIALEKTIK ──────────────────────────────────────────────────────
        contradictions = self._load_contradictions()
        if not contradictions:
            if result.pruned > 0:
                self._persist()
            logger.info("Dream: kein Widerspruch gefunden. %d bereinigt.", result.pruned)
            return result

        logger.info("Dream: %d Widersprüche → Synthese", len(contradictions))
        batch = self._select_batch(contradictions)
        doc_lookup = {str(d.get("id")): d for d in self._store.archive}

        for entry in batch:
            try:
                r = self._synthesize_one(entry, doc_lookup)
                if r == "crystal":
                    result.crystals += 1
                elif r == "skip":
                    result.skipped += 1
            except Exception as e:
                logger.warning("Dream-Meta-Synthese fehlgeschlagen: %s", e)
                result.errors += 1

        if result.crystals > 0 or result.pruned > 0:
            self._persist()

        logger.info("Dream abgeschlossen: %d Kristalle, %d bereinigt, %d übersprungen, %d Fehler",
                    result.crystals, result.pruned, result.skipped, result.errors)
        return result

    # ── Apoptose ──────────────────────────────────────────────────────────────

    def _collect_active_resonance_ids(self) -> set[str]:
        active: set[str] = set()
        if self._field is None:
            return active
        for node, edges in self._field.R.items():
            active.add(str(node))
            for target in edges:
                active.add(str(target))
        return active

    def _prune_dead_meta_nodes(self, active_ids: set[str]) -> int:
        n_before = len(self._store.archive)
        self._store.archive = [
            d for d in self._store.archive
            if not (d.get("generation", 0) > 0
                    and d.get("source") == META_SOURCE
                    and str(d.get("id", "")) not in active_ids)
        ]
        pruned = n_before - len(self._store.archive)
        if pruned > 0:
            self._store._doc_index = {str(d.get("id", i)): d for i, d in enumerate(self._store.archive)}
            logger.info("Apoptose: %d tote Meta-Knoten entfernt (%d → %d)",
                        pruned, n_before, len(self._store.archive))
        return pruned

    # ── Dialektik ─────────────────────────────────────────────────────────────

    def _load_contradictions(self) -> list[dict]:
        if not self._dist_file.exists():
            return []
        contradictions = []
        with open(self._dist_file, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # In collect2 vereinfacht: zone=FALLBACK → Widerspruch
                # (kein comparison.agreement-Feld im DistillationEntry)
                zone = entry.get("zone", "")
                if zone == "FALLBACK":
                    contradictions.append(entry)
        return contradictions

    def _classify_axiom(self, query: str, parents: list[dict]) -> dict:
        """Ordnet einen Widerspruch dem besten passenden Axiom zu (Keyword-Heuristik).
        Text = Query + Titel + Content der Eltern-Docs (erste 2000 Zeichen).
        """
        text = query.lower()
        for p in parents:
            text += " " + str(p.get("title", "")).lower()
            text += " " + str(p.get("content", ""))[:1000].lower()

        best_axiom = AXIOMS[0]
        best_score = 0

        for axiom in AXIOMS:
            score = sum(1 for kw in axiom["keywords"] if kw in text)
            if score > best_score:
                best_score = score
                best_axiom = axiom

        return best_axiom if best_score > 0 else AXIOMS[0]

    def _select_batch(self, contradictions: list[dict], batch_size: int = 3) -> list[dict]:
        doc_lookup = {str(d.get("id")): d for d in self._store.archive}
        unprocessed = []
        for e in contradictions:
            doc_ids = e.get("doc_ids", [])
            if not doc_ids:
                continue
            meta_id = _make_meta_id(doc_ids)
            if meta_id in doc_lookup:
                continue
            if len([did for did in doc_ids if did in doc_lookup]) < 2:
                continue
            unprocessed.append(e)

        meta_prio = [e for e in unprocessed if self._has_meta_parents(e.get("doc_ids", []), doc_lookup)]
        normal = [e for e in unprocessed if e not in meta_prio]

        logger.info("Dream-Batch: %d META-prio, %d normal (aus %d unverarbeitet)",
                    len(meta_prio), len(normal), len(unprocessed))

        n_meta = min(len(meta_prio), batch_size)
        batch = (random.sample(meta_prio, n_meta)
                 + random.sample(normal, min(len(normal), batch_size - n_meta)))
        return batch

    @staticmethod
    def _has_meta_parents(doc_ids: list[str], doc_lookup: dict[str, dict]) -> bool:
        for did in doc_ids:
            doc = doc_lookup.get(str(did))
            if doc and str(doc.get("id", "")).startswith(META_ID_PREFIX):
                return True
        return False

    def _synthesize_one(self, entry: dict, doc_lookup: dict[str, dict]) -> str:
        parent_ids = entry.get("doc_ids", [])
        parents = [doc_lookup[str(pid)] for pid in parent_ids if str(pid) in doc_lookup]
        if len(parents) < 2:
            return "skip"

        meta_id = _make_meta_id(parent_ids)
        if meta_id in doc_lookup:
            return "skip"

        max_gen = max(p.get("generation", 0) for p in parents)
        target_gen = max_gen + 1

        ctx = "\n\n".join([
            f"DOKUMENT {i+1}:\n{str(p.get('content', ''))[:800]}"
            for i, p in enumerate(parents)
        ])

        query = str(entry.get("query", ""))
        axiom = self._classify_axiom(query, parents)

        prompt = _build_meta_prompt(ctx, target_gen)
        t0 = time.perf_counter()

        try:
            r = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.65,
                        "num_predict": 800,
                        "repeat_penalty": 1.1,
                        "stop": ["<|im_end|>", "</s>"],
                    },
                },
                timeout=180,
            )
            meta_text = r.json().get("response", "").strip()
        except Exception as e:
            logger.warning("Dream-LLM-Fehler: %s", e)
            return "error"

        elapsed = time.perf_counter() - t0

        if not meta_text or len(meta_text) < 150:
            logger.debug("Dream: Synthese zu dünn (%d Zeichen) — übersprungen", len(meta_text))
            return "skip"

        entropy = _shannon_entropy(meta_text)
        compression = _kolmogorov_ratio(meta_text)

        enc = self._get_encoder()
        vec = enc.encode(meta_text, normalize_embeddings=False).astype("float32")

        parent_vecs = []
        for pid in parent_ids:
            spid = str(pid)
            if spid in self._store.doc_cache:
                parent_vecs.append(self._store.doc_cache[spid])
        drift = 0.0
        if parent_vecs:
            center = np.mean(parent_vecs, axis=0)
            drift = float(np.linalg.norm(vec - center))

        meta_node = {
            "id": meta_id,
            "source": META_SOURCE,
            "axiom": axiom["id"],
            "axiom_name": axiom["name"],
            "axiom_truth": axiom["hardware_truth"],
            "sector": f"DIALECTIC_GEN_{target_gen}",
            "title": f"[{axiom['id']}] Dialektischer Kristall G{target_gen} [{meta_id[-6:]}]",
            "content": meta_text,
            "generation": target_gen,
            "lineage": parent_ids,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "thermodynamics": {
                "latency_sec": round(elapsed, 2),
                "shannon_entropy": round(entropy, 4),
                "kolmogorov_ratio": round(compression, 4),
                "vector_drift": round(drift, 4),
            },
        }

        self._store.archive.append(meta_node)
        self._store.doc_cache[meta_id] = vec

        logger.info("Dream-Kristall: %s [%s G%d] | H=%.2f | Drift=%.2f",
                    meta_id, axiom["id"], target_gen, entropy, drift)
        return "crystal"

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _persist(self) -> None:
        from collect.retrieval.vault import Vault

        self._store._doc_index = {str(d.get("id", i)): d for i, d in enumerate(self._store.archive)}
        Vault(self._store.vault_file).save(self._store.archive)

        with open(self._store.cache_file, "wb") as f:
            pickle.dump(dict(self._store.doc_cache), f, protocol=4)

        self._store.rebuild_matrix()

        if self._field and self._store.doc_cache:
            self._field.register_documents(
                {k: v for k, v in self._store.doc_cache.items()
                 if isinstance(v, np.ndarray)}
            )
            self._field.save()

        logger.info("Dream: Vault + Cache + Feld persistiert")

    # ── Encoder (lazy) ────────────────────────────────────────────────────────

    def _get_encoder(self):
        if self._enc_model is None:
            from sentence_transformers import SentenceTransformer
            self._enc_model = SentenceTransformer(settings.embedding_model)
        return self._enc_model

    # ── Axiom-Übersicht ───────────────────────────────────────────────────────

    def axiom_summary(self) -> dict[str, dict]:
        counts: dict[str, dict] = {}
        for ax in AXIOMS:
            counts[ax["id"]] = {
                "name": ax["name"],
                "truth": ax["hardware_truth"],
                "total": 0,
                "by_generation": {},
                "alive": 0,
                "dead": 0,
            }
        active_ids: set[str] = set()
        if self._field:
            for node, edges in self._field.R.items():
                active_ids.add(str(node))
                active_ids.update(str(t) for t in edges)
        for doc in self._store.archive:
            if doc.get("source") != META_SOURCE:
                continue
            ax_id = doc.get("axiom", "?")
            if ax_id in counts:
                counts[ax_id]["total"] += 1
                gen = doc.get("generation", 0)
                counts[ax_id]["by_generation"][gen] = (
                    counts[ax_id]["by_generation"].get(gen, 0) + 1
                )
                if str(doc.get("id")) in active_ids:
                    counts[ax_id]["alive"] += 1
                else:
                    counts[ax_id]["dead"] += 1
        return counts
