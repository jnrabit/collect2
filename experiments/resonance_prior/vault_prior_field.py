"""VaultPriorField — ResonanceField mit Vault-Prior als Grundzustand statt 0.

EXPERIMENT, nicht Live. Erweitert das echte ResonanceField um genau eine Idee:
der Ruhezustand des Resonanz-Boosts ist nicht leer (R = {}), sondern ein aus der
Vault-Geometrie abgeleiteter Prior — ein Embedding-kNN-Graph. Damit hat der
γ·resonanz-Term schon bei Query 1 (ohne jede Nutzungshistorie) Signal.

Wichtig — was der Prior NICHT ist: kein zweiter Query-Doc-Cosinus. Der Boost
wirkt über Anker (Top-Cosinus-Treffer) und verteilt sich auf deren NACHBARN im
Embedding-Graphen. Das ist Kaltstart-Pseudo-Relevance-Feedback / Graph-Diffusion,
additiv zum direkten Cosinus (warp_arr).

Design:
  * prior wird getrennt von R gehalten -> _decay() (das R gegen 0 zieht) lässt
    den Prior als Boden stehen; gelernte Nutzung wächst darüber und dominiert
    mit der Zeit.
  * prior_strength skaliert den Prior schwach, damit er Kaltstart-Boden gibt,
    ohne den emergenten Teil zu übertönen.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from collect.retrieval.resonance import ResonanceField


class VaultPriorField(ResonanceField):
    def __init__(self, *args, prior_k: int = 5, prior_strength: float = 0.3,
                 **kwargs):
        self.prior: dict = defaultdict(lambda: defaultdict(float))
        self.prior_k = prior_k
        self.prior_strength = prior_strength
        super().__init__(*args, **kwargs)

    def seed_prior(self, doc_embeddings: dict) -> None:
        """Baut den Embedding-kNN-Graphen als Prior-Grundzustand."""
        ids = list(doc_embeddings)
        if len(ids) < 2:
            return
        M = np.stack([doc_embeddings[i].astype(np.float32) for i in ids])
        M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-8)
        sim = M @ M.T                      # Cosinus aller Paare
        np.fill_diagonal(sim, -1.0)        # Selbst-Treffer ausschließen
        k = min(self.prior_k, len(ids) - 1)
        for a, row in enumerate(sim):
            nbr = np.argpartition(row, -k)[-k:]
            for b in nbr:
                w = float(row[b])
                if w <= 0:
                    continue
                ia, ib = ids[a], ids[b]
                # symmetrisch, max statt Summe (stabil bei doppelter kNN-Kante)
                self.prior[ia][ib] = max(self.prior[ia][ib], w)
                self.prior[ib][ia] = max(self.prior[ib][ia], w)

    def get_resonance_boost(self, candidate_ids: list, anchor_ids: list) -> dict:
        # Gelernte Nutzung (Basisverhalten) ...
        boosts = defaultdict(float, super().get_resonance_boost(candidate_ids, anchor_ids))
        # ... plus Vault-Prior als Grundzustand.
        id_set = set(candidate_ids)
        for aid in anchor_ids:
            if aid in self.prior:
                for cid, val in self.prior[aid].items():
                    if cid in id_set:
                        boosts[cid] += self.prior_strength * val
        return dict(boosts)
