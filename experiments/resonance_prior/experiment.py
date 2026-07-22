"""Shadow-Experiment: hebt ein Vault-Prior-Grundzustand den Kaltstart-Recall?

Isoliert, kein Live-Pfad. Vergleicht auf einem synthetischen, geclusterten Vault
zwei Resonanzfeld-Grundzustaende bei NULL Nutzungshistorie (Kaltstart):
  BASE  = echtes ResonanceField, R leer -> res_arr = 0   (heutiges Verhalten)
  PRIOR = VaultPriorField, Embedding-kNN als Grundzustand

Scoring-Muster wie chaos.py: score = alpha*cosinus + gamma*res_arr,
res_arr = min(boost/(boost+3), 0.5), Anker = Top-Cosinus (retrieval_resonance_anchors).
thompson/exploration sind bei Kaltstart pro Dokument konstant und beeinflussen das
Ranking-Delta zwischen den Armen nicht -> weggelassen.

Kennzahl, die zaehlt: Recall der HARTEN Cluster-Mates — relevante Docs, die reiner
Cosinus NICHT in die Top-k bringt. Genau dort kann der Prior additiv wirken (Graph-
Diffusion von den Ankern), ohne bloss Query-Doc-Cosinus zu verdoppeln.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect.config import settings
from collect.retrieval.resonance import ResonanceField
from vault_prior_field import VaultPriorField

import os
EMBED_DIM = int(os.environ.get("X_DIM", 32))          # niedriger -> Centroids ueberlappen (Headroom)
N_CLUSTERS = int(os.environ.get("X_CLUST", 20))
DOCS_PER_CLUSTER = int(os.environ.get("X_DPC", 15))
N_QUERIES = int(os.environ.get("X_Q", 400))
SPREAD = float(os.environ.get("X_SPREAD", 0.9))       # Rausch-Norm rel. Centroid (Einheitsvektoren)
QUERY_OFFSET = float(os.environ.get("X_QOFF", 1.0))
TOP_K = int(os.environ.get("X_TOPK", 15))
SEED = 20260720


def make_vault(rng):
    centroids = rng.randn(N_CLUSTERS, EMBED_DIM).astype(np.float32)
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    embeddings, cluster_of = {}, {}
    did = 0
    for c in range(N_CLUSTERS):
        for _ in range(DOCS_PER_CLUSTER):
            v = centroids[c] + SPREAD * _unit(rng.randn(EMBED_DIM).astype(np.float32))
            v /= np.linalg.norm(v)
            embeddings[did] = v.astype(np.float32)
            cluster_of[did] = c
            did += 1
    return centroids, embeddings, cluster_of


def _unit(x):
    return x / (np.linalg.norm(x) + 1e-8)


def cosine_scores(qv, ids, emb):
    M = np.stack([emb[i] for i in ids])
    return M @ qv


def score_arm(field, qv, ids, emb, alpha, gamma, n_anchor):
    warp = cosine_scores(qv, ids, emb)
    order = np.argsort(warp)[::-1]
    anchors = [ids[i] for i in order[:n_anchor]]
    res = np.zeros(len(ids), dtype=np.float32)
    boosts = field.get_resonance_boost(ids, anchors)
    if boosts:
        raw = np.array([boosts.get(d, 0.0) for d in ids], dtype=np.float32)
        res = np.minimum(raw / (raw + 3.0), 0.5)
    return alpha * warp + gamma * res


def run_arms(centroids, emb, ids, members, base, prior, gamma, alpha, n_anchor, rng):
    agg = {"base": [], "prior": []}
    mrr = {"base": [], "prior": []}
    hard_total = hard_base = hard_prior = 0
    for _ in range(N_QUERIES):
        c = rng.randint(N_CLUSTERS)
        rel = set(members[c])
        qv = centroids[c] + QUERY_OFFSET * _unit(rng.randn(EMBED_DIM).astype(np.float32))
        qv /= np.linalg.norm(qv)
        cos = cosine_scores(qv, ids, emb)
        cos_top = {ids[i] for i in np.argsort(cos)[::-1][:TOP_K]}
        hard = rel - cos_top
        for name, field in (("base", base), ("prior", prior)):
            s = score_arm(field, qv, ids, emb, alpha, gamma, n_anchor)
            order = np.argsort(s)[::-1]
            topk = {ids[i] for i in order[:TOP_K]}
            agg[name].append(len(topk & rel) / len(rel))
            first = next((r for r, i in enumerate(order, 1) if ids[i] in rel), None)
            mrr[name].append(1.0 / first if first else 0.0)
            if name == "base":
                hard_base += len(hard & topk)
            else:
                hard_prior += len(hard & topk)
        hard_total += len(hard)
    m = lambda x: float(np.mean(x))
    return {
        "rec_base": m(agg["base"]), "rec_prior": m(agg["prior"]),
        "mrr_base": m(mrr["base"]), "mrr_prior": m(mrr["prior"]),
        "hard_total": hard_total, "hard_base": hard_base, "hard_prior": hard_prior,
    }


def build(emb, prior_strength):
    base = ResonanceField(Path(tempfile.mkdtemp()) / "b.pkl", n_lorenz_dims=8)
    base.register_documents(emb)
    prior = VaultPriorField(Path(tempfile.mkdtemp()) / "p.pkl", n_lorenz_dims=8,
                            prior_k=5, prior_strength=prior_strength)
    prior.register_documents(emb)
    prior.seed_prior(emb)
    return base, prior


def evaluate():
    rng = np.random.RandomState(SEED)
    centroids, emb, cluster_of = make_vault(rng)
    ids = list(emb)
    members = {c: [d for d in ids if cluster_of[d] == c] for c in range(N_CLUSTERS)}
    alpha = settings.retrieval_alpha
    n_anchor = min(settings.retrieval_resonance_anchors, len(ids))

    print("=" * 72)
    print(f" Vault-Prior-Grundzustand — Kaltstart (0 Nutzung), {N_QUERIES} Queries")
    print(f" {N_CLUSTERS} Cluster x {DOCS_PER_CLUSTER} = {len(ids)} Docs | alpha={alpha}"
          f" | anchors={n_anchor} | top_k={TOP_K} | spread={SPREAD}")
    print("=" * 72)

    # 1) Faithful: Default-gamma, moderater Prior
    base, prior = build(emb, prior_strength=0.3)
    r = run_arms(centroids, emb, ids, members, base, prior,
                 settings.retrieval_gamma, alpha, n_anchor, np.random.RandomState(1))
    print(f" [faithful] gamma={settings.retrieval_gamma} prior_strength=0.3")
    print(f"   Recall@{TOP_K}: BASE {r['rec_base']:.4f}  PRIOR {r['rec_prior']:.4f}"
          f"  Δ {r['rec_prior']-r['rec_base']:+.4f}")
    print(f"   MRR:       BASE {r['mrr_base']:.4f}  PRIOR {r['mrr_prior']:.4f}"
          f"  Δ {r['mrr_prior']-r['mrr_base']:+.4f}")
    print(f"   HARTE Mates {r['hard_total']}: BASE holt {r['hard_base']}"
          f" ({r['hard_base']/max(r['hard_total'],1)*100:.1f}%)"
          f" | PRIOR holt {r['hard_prior']}"
          f" ({r['hard_prior']/max(r['hard_total'],1)*100:.1f}%)")

    # 2) Sweep: wie hoch KANN der Prior heben, wenn Gewicht/Staerke steigen?
    print("-" * 72)
    print(" Sweep (Decke des Prior-Effekts auf Recall@%d):" % TOP_K)
    print("   gamma  p_str |  BASE   PRIOR    Δrecall   harte-Mates PRIOR%")
    for gamma in (0.10, 0.30, 0.60):
        for ps in (0.3, 1.0, 3.0):
            b2, p2 = build(emb, prior_strength=ps)
            rr = run_arms(centroids, emb, ids, members, b2, p2,
                          gamma, alpha, n_anchor, np.random.RandomState(1))
            print(f"   {gamma:<5}  {ps:<4} | {rr['rec_base']:.3f}  {rr['rec_prior']:.3f}"
                  f"   {rr['rec_prior']-rr['rec_base']:+.4f}"
                  f"    {rr['hard_prior']/max(rr['hard_total'],1)*100:5.1f}%")
    print("=" * 72)
    print(" Lesart: Δrecall = Nutzen des Vault-Grundzustands ggü. res=0 bei Kaltstart.")
    print(" HARTE-Mates-% = Anteil der von reinem Cosinus verfehlten Cluster-Mates,")
    print(" die der Prior via Graph-Diffusion doch in Top-k holt (der additive Teil).")


if __name__ == "__main__":
    evaluate()
