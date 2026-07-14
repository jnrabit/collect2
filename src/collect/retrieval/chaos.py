"""ChaosRetrieval — Thompson Sampling + Riemannscher Chaos-Warp + Resonanz.

Port aus vibelike (intelligence/retrieval.py); Scoring-Mathematik unverändert,
aber Protocol-Kopplung aufgelöst: Engine (Hardware-State) und Store (Matrix)
werden injiziert. Vollständig vektorisiert, kein Python-Loop über Dokumente.

    score(d) = α·warp(d) + β·thompson(d) + γ·resonanz(d) + δ·exploration(d)

α,β,γ,δ werden durch Hardware-Entropie moduliert: hohe Entropie → mehr
Exploration, niedrige → mehr Exploitation. Distanz-Semantik der Ergebnisse:
dist = (1 - score) * 100 — darauf sind die Drei-Zonen-Schwellen kalibriert.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Optional

import numpy as np

from collect.retrieval.native import NativeEngine
from collect.retrieval.resonance import ResonanceField
from collect.retrieval.store import VaultStore

logger = logging.getLogger(__name__)


class ThompsonSampler:
    """Beta(α, β) pro Dokument. Neue Docs → Beta(1,1) = maximale Exploration.

    seed: optional für reproduzierbare Tests; Produktion bleibt unseeded.
    """

    def __init__(self, seed: Optional[int] = None):
        self.alpha = defaultdict(lambda: 1.0)
        self.beta = defaultdict(lambda: 1.0)
        self.rng = np.random.RandomState(seed)

    def batch_sample(self, id_map: list) -> np.ndarray:
        a = np.array([self.alpha[d] for d in id_map], dtype=np.float32)
        b = np.array([self.beta[d] for d in id_map], dtype=np.float32)
        return self.rng.beta(a, b).astype(np.float32)

    def batch_mean(self, id_map: list) -> np.ndarray:
        """Posterior-Mean a/(a+b) — deterministisch. Frische Docs → 0.5 (kein
        Rauschen); Feedback verschiebt den Mean wie beim Sampling."""
        a = np.array([self.alpha[d] for d in id_map], dtype=np.float32)
        b = np.array([self.beta[d] for d in id_map], dtype=np.float32)
        return (a / (a + b)).astype(np.float32)

    def exploration_scores(self, id_map: list) -> np.ndarray:
        a = np.array([self.alpha[d] for d in id_map], dtype=np.float32)
        b = np.array([self.beta[d] for d in id_map], dtype=np.float32)
        return (1.0 / (1.0 + (a + b - 2.0) * 0.1)).astype(np.float32)

    def update(self, retrieved: list, relevant: set) -> None:
        for did in retrieved:
            if did in relevant:
                self.alpha[did] += 1.0
            else:
                self.beta[did] += 0.3


class RiemannianWarp:
    """Zeitabhängige Metrik: score(q, d, t) = cos(q⊙w(t), d⊙w(t)).

    w(t) wird aus den Lorenz-Koordinaten + Resonanz-Kraft projiziert
    (Johnson-Lindenstrauss, fixierter Seed 1337, orthogonalisiert).
    """

    def __init__(self, embed_dim: int = 384, lorenz_dims: int = 8):
        self.embed_dim = embed_dim
        self.lorenz_dims = lorenz_dims
        rng = np.random.RandomState(1337)
        P = rng.randn(embed_dim, lorenz_dims).astype(np.float32)
        U, _, _ = np.linalg.svd(P, full_matrices=False)
        self._P = U.astype(np.float32)
        self._warp = np.ones(embed_dim, dtype=np.float32)
        self.warp_history: list = []

    def update(self, lorenz_state: dict, resonance_force: np.ndarray = None) -> None:
        sv = np.array([
            lorenz_state.get("x1", 0), lorenz_state.get("y1", 0),
            lorenz_state.get("z1", 0), lorenz_state.get("w1", 0),
            lorenz_state.get("x2", 0), lorenz_state.get("y2", 0),
            lorenz_state.get("z2", 0), lorenz_state.get("w2", 0),
        ], dtype=np.float32)[:self.lorenz_dims]

        raw = self._P @ sv
        if resonance_force is not None and len(resonance_force) == self.lorenz_dims:
            raw += self._P @ resonance_force.astype(np.float32) * 0.3

        wn = np.tanh(raw / 20.0).astype(np.float32)
        self._warp = (1.0 + wn * 0.3).astype(np.float32)
        self.warp_history.append(float(-np.sum(
            np.abs(wn) * np.log(np.abs(wn) + 1e-8)
        )))
        if len(self.warp_history) > 100:
            self.warp_history.pop(0)

    def score(self, q: np.ndarray, doc_matrix: np.ndarray) -> np.ndarray:
        qw = q * self._warp
        Dw = doc_matrix * self._warp[np.newaxis, :]
        qw_sq = np.sum(qw ** 2)
        Dw_sq = np.sum(Dw ** 2, axis=1)
        dot_products = np.einsum("d,nd->n", qw, Dw)
        return (dot_products / np.sqrt(qw_sq * Dw_sq + 1e-16)).astype(np.float32)

    @property
    def divergence(self) -> float:
        return float(np.std(self._warp))


class ChaosRetrieval:
    def __init__(self, store: VaultStore, engine: Optional[NativeEngine] = None,
                 field: Optional[ResonanceField] = None,
                 embed_dim: int = 384, lorenz_dims: int = 8,
                 thompson_seed: Optional[int] = None,
                 deterministic: Optional[bool] = None):
        from collect.config import settings
        self.store = store
        self.engine = engine
        self.field = field
        self.deterministic = (settings.retrieval_deterministic
                              if deterministic is None else deterministic)
        self.warp = RiemannianWarp(embed_dim, lorenz_dims)
        self.thompson = ThompsonSampler(seed=thompson_seed)
        if self.deterministic:
            self.alpha, self.beta, self.gamma, self.delta = 0.8, 0.05, 0.1, 0.05
        else:
            self.alpha, self.beta, self.gamma, self.delta = 0.5, 0.2, 0.2, 0.1
        self._last_retrieved: list = []
        self._search_count: int = 0
        self._adaptive_count: int = 0

    def search(self, query_vec: np.ndarray, top_k: int = 30,
               profile: Optional[dict] = None) -> list[tuple]:
        """→ [(doc_id, distance)]. profile überschreibt die Init-Gewichte
        (null → Init-Gewichte / Settings)."""
        self._search_count += 1
        if self._search_count % 50 == 0:
            self._adapt_lorenz()

        t0 = time.perf_counter()
        doc_matrix, id_map = self.store.matrix, self.store.id_map
        if doc_matrix is None:
            return []
        n = len(id_map)
        top_k = min(top_k, n)

        lorenz_state, entropy = {}, 0.5
        if self.engine and self.engine.active:
            s = self.engine.get_hardware_state()
            lorenz_state = s
            entropy = min(1.0, s.get("entropy", 4.0) / 8.0)

        use_warp = self.deterministic is False
        use_sample = not self.deterministic
        a, b, g, d = self.alpha, self.beta, self.gamma, self.delta

        if profile:
            if profile.get("adaptive"):
                self._adaptive_count += 1
                if self._adaptive_count < 20:
                    a, b, g, d = 0.55, 0.15, 0.10, 0.20
                    use_sample = True
                elif self._adaptive_count < 80:
                    a, b, g, d = 0.70, 0.08, 0.15, 0.07
                    use_sample = False
                else:
                    a, b, g, d = 0.90, 0.02, 0.05, 0.03
                    use_sample = False
            else:
                a = profile.get("alpha", a)
                b = profile.get("beta", b)
                g = profile.get("gamma", g)
                d = profile.get("delta", d)
                use_warp = profile.get("warp", use_warp)
                use_sample = profile.get("sampling", use_sample)

        if use_warp:
            r_force = None
            if self.field:
                lp = np.array([
                    lorenz_state.get("x1", 0), lorenz_state.get("y1", 0),
                    lorenz_state.get("z1", 0), lorenz_state.get("w1", 0),
                    lorenz_state.get("x2", 0), lorenz_state.get("y2", 0),
                    lorenz_state.get("z2", 0), lorenz_state.get("w2", 0),
                ], dtype=np.float32)
                r_force = self.field.get_lorenz_force(lp)
            self.warp.update(lorenz_state, r_force)
            warp_arr = self.warp.score(query_vec.astype(np.float32), doc_matrix)
            thomp_arr = self.thompson.batch_sample(id_map) if use_sample else self.thompson.batch_mean(id_map)
        else:
            warp_arr = self.warp.score(query_vec.astype(np.float32), doc_matrix)
            thomp_arr = self.thompson.batch_sample(id_map) if use_sample else self.thompson.batch_mean(id_map)
        explor_arr = self.thompson.exploration_scores(id_map)

        n_anchor = min(20, n)
        top20_indices = np.argpartition(warp_arr, -n_anchor)[-n_anchor:]
        top20_indices = top20_indices[np.argsort(warp_arr[top20_indices])[::-1]]
        top20_ids = [id_map[i] for i in top20_indices]
        res_arr = np.zeros(n, dtype=np.float32)
        if self.field:
            boosts = self.field.get_resonance_boost(id_map, top20_ids)
            if boosts:
                res_arr = np.array([boosts.get(d, 0.0) for d in id_map], dtype=np.float32)
                res_arr = np.minimum(res_arr / (res_arr + 3.0), 0.5)

        if use_warp and self.deterministic is False:
            exp_mode = entropy
            expl_mode = 1.0 - entropy
            a = a * expl_mode + 0.3 * exp_mode
            b = b * exp_mode + 0.1 * expl_mode
            g = g * expl_mode + 0.1 * exp_mode
            d = d * exp_mode + 0.05 * expl_mode

        scores = a * warp_arr + b * thomp_arr + g * res_arr + d * explor_arr
        top_k_indices = np.argpartition(scores, -top_k)[-top_k:]
        top_k_indices = top_k_indices[np.argsort(scores[top_k_indices])[::-1]]

        results, retrieved = [], []
        for idx in top_k_indices:
            did = id_map[idx]
            dist = max(0.0, (1.0 - float(scores[idx])) * 100.0)
            results.append((did, dist))
            retrieved.append(did)

        if self.field:
            self.field.record_activation(retrieved[:15], query_vec)

        self._last_retrieved = retrieved
        logger.debug("%s docs → top %d | %.1fms | warp=%.4f | H=%.3f",
                     f"{n:,}", len(results),
                     (time.perf_counter() - t0) * 1000,
                     self.warp.divergence, entropy)
        return results

    def _adapt_lorenz(self) -> None:
        """Schaltet Lorenz-Parameter der Engine anhand des Resonanzfeld-Zustands.

        reason = tracked_pairs/queries (geclippt): niedrig → EXPLORE (rho hoch),
        hoch → EXPLOIT (rho niedrig). rho_target ∈ [18, 38]. Fehler werden
        geloggt, nie propagiert — search() darf an der Adaption nicht sterben.
        """
        if not (self.engine and self.engine.active and self.field):
            return
        try:
            fs = self.field.get_stats()
            reason = float(np.clip(
                fs.get("tracked_pairs", 0) / max(fs.get("queries", 1), 1), 0.0, 1.0))
            rho_target = 28.0 + (reason - 0.5) * 20.0
            self.engine.set_lorenz_params(rho_target, 10.0, 8.0 / 3.0, reason)
            cycle = self.engine.get_lorenz_params().get("cycle", 0)
            logger.info("Lorenz adapt #%d: reason=%.3f → ρ=%.2f", cycle, reason, rho_target)
        except Exception as e:
            logger.warning("Lorenz-Adaption fehlgeschlagen: %s: %s", type(e).__name__, e)

    def feedback(self, relevant_ids: list) -> None:
        self.thompson.update(self._last_retrieved, set(relevant_ids))
        if self.engine:
            self.engine.apply_cortex_feedback(False)

    def diagnostics(self) -> dict:
        return {
            "search_count": self._search_count,
            "warp_divergence": self.warp.divergence,
            "warp_history": self.warp.warp_history[-50:],
            "thompson": {
                "tracked": len(self.thompson.alpha),
                "high_confidence": sum(
                    1 for d in self.thompson.alpha
                    if self.thompson.alpha[d] + self.thompson.beta[d] > 5
                ),
            },
        }
