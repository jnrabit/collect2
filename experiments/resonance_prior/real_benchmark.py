"""Prior-Vergleichsarm auf dem ECHTEN Vault (259k Docs, echte Embeddings).

Shadow, read-only: lädt den General-Vault + echtes Modell, führt die
collect.eval.EVAL_QUERIES im Kaltstart (0 Nutzung) durch und vergleicht
  BASE  = reiner Cosinus (res=0, heutiger Grundzustand)
  PRIOR = Cosinus + gamma*res, res-Grundzustand aus Vault-kNN der Anker

Kennzahl: Begriff-Präsenz@k (EVAL_QUERIES.terms in den Top-k Doc-Texten) für
k in {3,10,30}. Das ist die vorhandene Relevanz-Proxy des Projekts — grob, aber
echt. Ehrlicher Vorbehalt: der Prior hilft eher tiefe Recall-Vollständigkeit als
den Spitzenrang; term@3 kann daher unbewegt bleiben, term@30 eher nicht.

Läuft NICHT im Live-Pfad; ändert nichts an collect2.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from collect.config import settings
from collect.eval import EVAL_QUERIES
from collect.retrieval.embedding import EmbeddingBackend
from collect.retrieval.store import VaultStore

GAMMA = settings.retrieval_gamma
N_ANCHOR = settings.retrieval_resonance_anchors
PRIOR_K = 8
PRIOR_STRENGTH = 1.0
KS = (3, 10, 30)


def _l2(m):
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-8)


def doc_text(store, did):
    d = store._doc_index.get(did, {})
    return (d.get("title", "") + " " + d.get("content", "")[:400]).lower()


def rank(scores, id_map, k):
    idx = np.argpartition(scores, -k)[-k:]
    idx = idx[np.argsort(scores[idx])[::-1]]
    return [id_map[i] for i in idx]


def term_hit(store, ids, terms):
    blob = " ".join(doc_text(store, d) for d in ids)
    return any(t.lower() in blob for t in terms)


def main():
    t0 = time.time()
    print("Lade General-Vault (259k) …", flush=True)
    store = VaultStore(settings.knowledge_vault_file, settings.knowledge_cache_file)
    M = _l2(store.matrix.astype(np.float32))
    id_map = store.id_map
    print(f"  {len(id_map):,} Docs, Matrix {M.shape}, {time.time()-t0:.1f}s", flush=True)

    print("Lade Embedding-Modell …", flush=True)
    emb = EmbeddingBackend()
    queries = [q for q in EVAL_QUERIES if q.get("terms")]
    qmat = _l2(np.stack([emb.embed_one(q["query"]).astype(np.float32) for q in queries]))
    print(f"  {len(queries)} Queries embedded, {time.time()-t0:.1f}s\n", flush=True)

    agg = {arm: {k: 0 for k in KS} for arm in ("base", "prior")}
    print(f"{'Query':<46} " + " ".join(f'B@{k:<2} P@{k:<2}' for k in KS))
    print("-" * 90)

    for qi, q in enumerate(queries):
        warp = M @ qmat[qi]                      # Cosinus über den ganzen Vault
        anchors = np.argpartition(warp, -N_ANCHOR)[-N_ANCHOR:]

        # Vault-Prior: fuer jeden Anker seine kNN-Nachbarn -> res-Grundzustand
        boost = np.zeros(len(id_map), dtype=np.float32)
        for ai in anchors:
            sims = M @ M[ai]
            nbr = np.argpartition(sims, -(PRIOR_K + 1))[-(PRIOR_K + 1):]
            for j in nbr:
                if j != ai and sims[j] > 0:
                    boost[j] += PRIOR_STRENGTH * float(sims[j])
        res = np.minimum(boost / (boost + 3.0), 0.5)

        base_scores = warp
        prior_scores = warp + GAMMA * res       # alpha kürzt sich im Ranking-Vergleich

        cells = []
        for k in KS:
            bh = term_hit(store, rank(base_scores, id_map, k), q["terms"])
            ph = term_hit(store, rank(prior_scores, id_map, k), q["terms"])
            agg["base"][k] += bh
            agg["prior"][k] += ph
            cells.append(f"{'✓' if bh else '·':<3} {'✓' if ph else '·':<3}")
        print(f"{q['query'][:45]:<46} " + " ".join(cells))

    n = len(queries)
    print("-" * 90)
    print(f"{'Begriff-Präsenz (Treffer / ' + str(n) + ')':<46} "
          + " ".join(f"{agg['base'][k]:>2}   {agg['prior'][k]:>2}" for k in KS))
    print(f"\ngamma={GAMMA} anchors={N_ANCHOR} prior_k={PRIOR_K} strength={PRIOR_STRENGTH}"
          f" | Gesamtzeit {time.time()-t0:.1f}s")
    print("Spalten je k:  B=Baseline (res=0)   P=Prior (Vault-Grundzustand)")


if __name__ == "__main__":
    main()
