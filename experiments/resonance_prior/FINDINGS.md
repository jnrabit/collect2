# Vault-Prior als Resonanzfeld-Grundzustand — Befunde

**Experiment, isoliert. Kein Live-Pfad berührt** (eigener `experiments/`-Ordner, nichts im
laufenden Dienst importiert das). Stand 2026-07-20.

## Frage
Heute ist der Grundzustand des `ResonanceField` **0**: `res_arr` startet als `np.zeros(n)`
(`chaos.py:212`), `get_resonance_boost` liest nur gelernte Nutzung `R` (bei Kaltstart leer,
per `_decay()` gegen 0). Jakobs Idee: den Grundzustand **mit Vault-Daten flushen statt 0** —
ein Embedding-kNN-Graph als Prior, damit der γ·resonanz-Term schon bei Query 1 Signal hat.

## Aufbau
Synthetischer geclusterter Vault (20 Cluster). Kaltstart (0 Nutzung). Scoring wie `chaos.py`:
`score = α·cosinus + γ·res_arr`, `res_arr = min(boost/(boost+3), 0.5)`, Anker = Top-Cosinus.
Baseline = echtes `ResonanceField` (res=0). Prior = `VaultPriorField` (kNN-Grundzustand).
Kennzahl: Recall@15 + speziell **harte Cluster-Mates** = relevante Docs, die reiner Cosinus
NICHT in Top-k bringt (dort kann nur ein additiver Effekt wirken; Baseline holt sie zu 0%).

## Ergebnis (dim=32, spread=1.3, Baseline-Recall 0.694)

| gamma | prior_strength | Recall BASE | Recall PRIOR | Δ | harte Mates geholt |
|--:|--:|--:|--:|--:|--:|
| 0.1 (Live-Default) | 0.3 | 0.694 | 0.713 | **+0.019** | 7.2% |
| 0.3 | 1.0 | 0.694 | 0.783 | +0.089 | 32.6% |
| 0.6 | 1.0 | 0.694 | 0.812 | **+0.118** | 44.3% |
| 0.6 | 3.0 | 0.694 | 0.773 | +0.079 | 29.2% |

Härteres Regime (dim=16, Baseline 0.431): gleiche Richtung, kleiner (bestes Δ +0.037).

## Was das ehrlich heißt
1. **Der Mechanismus trägt.** Der Prior holt gezielt *harte* Cluster-Mates in die Top-k, die
   reiner Cosinus verfehlt (Baseline: 0%, Prior: bis 44%). Das ist echte Graph-Diffusion von
   den Ankern — **additiv zum Query-Doc-Cosinus**, nicht dessen Verdopplung. Die Idee ist gut.
2. **Beim Live-Gewicht ist der Effekt klein** (γ=0.1 → +0.019). Resonanz ist im System bewusst
   ein leiser Nudge; der Prior erbt diese Leisheit. Spürbar wird er erst, wenn man γ und
   prior_strength anhebt — das handelt aber gegen die Reinheit von „Resonanz = gelernte
   Nutzung" und braucht Validierung auf echten Daten.
3. **Es gibt einen Sweet Spot.** prior_strength=3.0 ist überall schlechter als 1.0 — zu starker
   Prior promotet falsche Nachbarn. Also ein tunbarer Knopf mit Optimum, nicht gratis.
4. **MRR bewegt sich kaum** — der Prior verbessert die *Vollständigkeit* des Clusters, nicht den
   Spitzenrang. Genau das Verhalten von Pseudo-Relevance-Feedback.

## Grenze / nächster Schritt
Diese Zahlen sind eine **optimistische Obergrenze**: der synthetische Vault hat perfekte
Cluster-Labels, und der Prior-kNN wird aus derselben Geometrie gebaut, die Relevanz definiert.
Auf dem echten Vault ist Relevanz ≠ Embedding-Cluster. Der ehrliche nächste Test: den Prior in
`tests/test_retrieval_quality.py` gegen echte Relevanz-Urteile hängen (weiter im Shadow), und
prüfen, ob der Kaltstart-Gewinn dort bestehen bleibt — ohne den Resonanz-vs-Cosinus-Vergleich
zu verfälschen.

## Dateien
- `vault_prior_field.py` — `ResonanceField`-Subklasse mit kNN-Prior-Grundzustand (Prior getrennt
  von `R`, damit `_decay` ihn als Boden stehen lässt).
- `experiment.py` — synthetischer Vault + Kaltstart-Vergleich + γ/prior_strength-Sweep.
  Geometrie via Env: `X_DIM`, `X_SPREAD`, `X_QOFF`, `X_TOPK`, `X_CLUST`, `X_DPC`, `X_Q`.
